"""The transport modes a template (and a tenant's licence to use one) is tagged with.

One list, imported everywhere a mode is validated or offered, so the set of valid values
cannot drift between the tenant-creation picker, the template wizard, and the email
pipeline's safety check.
"""

MODES = [
    "Train Export", "Train Import",
    "Air Export", "Air Import",
    "Sea Import", "Sea Export",
    "Road Export", "Road Import",
]
