"""rotate_secret: every encrypted column moves from the old key to the new one, in one go;
safe to run twice; refuses (writing nothing) when a value opens with neither key."""
import pytest
from sqlalchemy import text

from app.core import rotate_secret as rs

OLD, NEW = "change-me-to-a-long-random-string", "n" * 48


@pytest.fixture
def db(db_session, monkeypatch):
    """Point COLUMNS at small temp tables with the same shape, one value each."""
    old = rs.fernet_for(OLD)
    cols = []
    for i, (table, pk, col) in enumerate(rs.COLUMNS):
        name = f"rot_{i}"
        db_session.execute(text(f"create table {name} (id text primary key, {col} text)"))
        db_session.execute(text(f"insert into {name} values ('a', :v), ('b', null), ('c', '')"),
                           {"v": old.encrypt(f"secret-{i}".encode()).decode()})
        cols.append((name, "id", col))
    db_session.commit()
    monkeypatch.setattr(rs, "COLUMNS", cols)
    return db_session


def test_columns_match_the_real_schema(db_session):
    for table, pk, col in rs.COLUMNS:
        names = [r[1] for r in db_session.execute(text(f"pragma table_info({table})")).all()]
        assert col in names and pk in names, f"{table}.{col}"


def test_apply_moves_every_value_and_keeps_plaintext(db):
    old, new = rs.fernet_for(OLD), rs.fernet_for(NEW)
    before = rs.scan(db, old, new)
    assert all(r["total"] == 1 and r["old"] == 1 for r in before)
    assert rs.apply(db, old, new) == len(rs.COLUMNS)
    db.commit()
    for i, (table, pk, col) in enumerate(rs.COLUMNS):
        tok = db.execute(text(f"select {col} from {table} where id='a'")).scalar()
        assert new.decrypt(tok.encode()).decode() == f"secret-{i}"
    assert all(r["new"] == 1 and r["old"] == 0 for r in rs.scan(db, old, new))


def test_second_run_changes_nothing(db):
    old, new = rs.fernet_for(OLD), rs.fernet_for(NEW)
    rs.apply(db, old, new)
    db.commit()
    assert rs.apply(db, old, new) == 0


def test_unreadable_value_aborts_without_writing(db):
    old, new = rs.fernet_for(OLD), rs.fernet_for(NEW)
    table, pk, col = rs.COLUMNS[0]
    db.execute(text(f"insert into {table} values ('z', :v)"),
               {"v": rs.fernet_for("some-third-key-xxxxxxxxxxxxxxxxx").encrypt(b"x").decode()})
    db.commit()
    with pytest.raises(RuntimeError, match="unreadable"):
        rs.apply(db, old, new)
    db.rollback()
    assert all(r["new"] == 0 for r in rs.scan(db, old, new))


def test_new_key_must_differ_and_be_long(tmp_path, monkeypatch):
    monkeypatch.setenv("OLD_JWT_SECRET", OLD)
    f = tmp_path / "k"
    f.write_text("short")
    monkeypatch.setenv("NEW_JWT_SECRET_FILE", str(f))
    with pytest.raises(SystemExit):
        rs._keys()
    f.write_text(OLD)
    with pytest.raises(SystemExit):
        rs._keys()
    f.write_text(NEW + "\n")
    assert rs._keys() == (OLD, NEW)
