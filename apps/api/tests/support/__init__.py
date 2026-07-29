"""Test infrastructure that is never imported by the application.

Everything here exists to make the suite deterministic and offline while the
application itself only ever speaks real HTTP to a real endpoint. Nothing under
`app/` may import from this package - `test_pvgis_stub.py` asserts it.
"""
