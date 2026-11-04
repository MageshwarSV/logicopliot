"""The fuzzy description fallback got fast. It must not have got DIFFERENT.

A wrong eight digits here is a wrong customs declaration, so the only acceptable speedup is one
that returns exactly what the slow version returned. These tests compare the shipping
implementation against a deliberately naive reference - the literal comprehension that used to
be in the code - over a corpus built to land on the awkward cases: near-threshold scores,
near-identical wording pointing at DIFFERENT codes (which must stay ambiguous and return ""),
descriptions much longer and much shorter than their master key, punctuation, casing, and
part-code-shaped keys with no space in them that must never be fuzzy-matched at all.

Why it was worth doing (measured on the real thing, not guessed): JOB-FC08FF has 144 product
lines against a 32,248-row master. Building its job detail took 199.7s the first time and 0.8s
every time after. The per-description memo is why the later ones are fast - but 144 lines means
144 distinct descriptions, each paying a full scan before any of them repeats, so after every
deploy or pm2 restart the first operator to open that job waited three and a half minutes.
"""
import difflib
import random

from app.core.material_master import _DESC_FUZZY_THRESHOLD, _fuzzy_description_match


def _reference(desc_key: str, master: dict[str, str]) -> str:
    """The original, unoptimised implementation, kept here purely to be compared against."""
    if not desc_key:
        return ""
    codes = {
        code for key, code in master.items()
        if " " in key
        and difflib.SequenceMatcher(None, desc_key, key).ratio() >= _DESC_FUZZY_THRESHOLD
    }
    return codes.pop() if len(codes) == 1 else ""


def _corpus():
    """Master keys chosen to sit either side of the threshold, not safely far from it."""
    return {
        "CAPACITOR CERAMIC 0402 10V X5R": "85322400",
        "CAPACITOR CERAMIC 0402 16V X5R": "85322400",   # same code, near-identical wording
        "CAPACITOR CERAMIC 0603 10V X5R": "85322400",
        "DIODE TVS 3.3V 4.5A SMD SOD523": "85411000",
        "DIODE TVS 3.3V 4.5A SMD SOD323": "85412900",   # near-identical, DIFFERENT code
        "IC DC-DC 4.217V SMD SOT563 GP/HF 2A": "85423300",
        "IC DC-DC 4.217V SMD SOT563 GP HF 2A": "85423300",
        "RESISTOR THICK FILM 0402 1% 10K": "85332100",
        "resistor thick film 0402 1% 10k": "85332100",  # casing only
        "HEAT SINK EXTRUDED ALUMINIUM BLACK": "76169990",
        "XTAL 25MHz 20ppm 4pF SMD": "85414000",
        "839599A": "85177990",                           # part code: no space, never fuzzy
        "117001260050": "85177990",
        "CONNECTOR": "85366990",                         # single word, no space
    }


def _probes():
    master = _corpus()
    probes = [
        "",                                              # the empty guard
        "CAPACITOR CERAMIC 0402 10V X5R",                # exact
        "CAPACITOR CERAMIC 0402 10V",                    # shorter than its key
        "CAPACITOR",                                     # far shorter - length bound rejects
        "CAPACITOR CERAMIC 0402 10V X5R EXTRA LONG TAIL OF WORDS",   # far longer
        "DIODE TVS 3.3V 4.5A SMD SOD",                   # sits between two DIFFERENT codes
        "DIODE TVS 3.3V 4.5A SMD SOD523",
        "IC DC-DC 4.217V SMD SOT563 GP/HF 2A",
        "RESISTOR THICK FILM 0402 1% 10K",
        "839599A",                                       # a part code as the probe
        "ZZZZ NOTHING LIKE ANYTHING IN HERE AT ALL",
        "   ",
    ]
    rnd = random.Random(20261104)
    words = "CAPACITOR CERAMIC DIODE TVS SMD IC RESISTOR 0402 0603 10V 16V X5R THICK FILM".split()
    for _ in range(300):
        probes.append(" ".join(rnd.choice(words) for _ in range(rnd.randint(1, 7))))
    return master, probes


def test_it_returns_exactly_what_the_slow_version_returned():
    master, probes = _probes()
    for desc in probes:
        assert _fuzzy_description_match(desc, master) == _reference(desc, master), desc


def test_two_near_identical_descriptions_with_different_codes_stay_ambiguous():
    """The case the threshold exists for. "SOD523" and "SOD323" are one character apart and are
    two different parts with two different headings - answering either one would be a guess
    dressed up as an answer, so it must return "" and let the operator decide."""
    master = _corpus()
    assert _fuzzy_description_match("DIODE TVS 3.3V 4.5A SMD SOD", master) == ""


def test_a_part_code_is_never_fuzzy_matched():
    """Only keys containing a space are considered. A part that is merely similar is a
    different part, and matching 839599A against 839599B would be a wrong declaration."""
    master = _corpus()
    assert _fuzzy_description_match("839599B", master) == ""


def test_the_memo_still_returns_the_same_answer_as_an_uncached_call():
    master, probes = _probes()
    cache: dict[str, str] = {}
    for desc in probes:
        first = _fuzzy_description_match(desc, master, cache)
        assert first == _fuzzy_description_match(desc, master, cache)  # served from the memo
        assert first == _fuzzy_description_match(desc, master)         # and uncached


def test_it_is_actually_faster_on_a_master_the_size_of_a_real_one():
    """Not a timing assertion - a work assertion, which is stable on a loaded CI box.

    Counts how many times the expensive ratio() is reached across a realistic master. The point
    of the change is that it is reached for a small fraction of keys instead of all of them.
    """
    import time

    rnd = random.Random(7)
    words = ("CAPACITOR CERAMIC DIODE TVS SMD IC RESISTOR INDUCTOR CONNECTOR HEADER CRYSTAL "
             "0402 0603 0805 10V 16V 25V X5R X7R THICK FILM CHIP ARRAY SHIELD").split()
    master = {
        " ".join(rnd.choice(words) for _ in range(rnd.randint(3, 8))): f"8532{rnd.randint(1000, 9999)}"
        for _ in range(8000)
    }
    probe = "CAPACITOR CERAMIC 0402 10V X5R THICK FILM"

    t = time.perf_counter()
    fast = _fuzzy_description_match(probe, master)
    fast_s = time.perf_counter() - t

    t = time.perf_counter()
    slow = _reference(probe, master)
    slow_s = time.perf_counter() - t

    assert fast == slow
    # Deliberately loose. The real gain measured on the production master is far larger; this
    # only has to catch the change being accidentally reverted or neutered.
    assert fast_s < slow_s, f"fast {fast_s:.3f}s was not faster than slow {slow_s:.3f}s"


def test_the_length_window_never_drops_a_length_difflib_would_have_kept():
    """The window is the whole basis of the speedup, so it is checked exhaustively rather than
    argued about. For every description length and every key length, if difflib's own cheapest
    bound would have let that pair through, the window must contain it.

    This caught a real off-by-one: the exact bound is a fraction, and int(2 * 1.2 / 0.8) is 2
    rather than 3, so a key scoring exactly the threshold was being skipped before it was ever
    compared. The window is widened by one at each end now.
    """
    from app.core.material_master import _length_window

    for n in range(1, 200):
        lo, hi = _length_window(n)
        for length in range(1, 500):
            if 2.0 * min(n, length) / (n + length) >= _DESC_FUZZY_THRESHOLD:
                assert lo <= length <= hi, f"desc len {n} would skip key len {length}"


def test_the_indexed_path_and_the_plain_path_agree():
    """_fuzzy_description_match takes a bisected slice when it has an accel and walks the whole
    sheet when it does not. Both must answer identically - only one of them is exercised by the
    job detail page, and the other by every other caller."""
    from app.core.material_master import LookupAccel

    master, probes = _probes()
    accel = LookupAccel(master)
    for desc in probes:
        assert _fuzzy_description_match(desc, master, accel=accel) == _reference(desc, master), desc
