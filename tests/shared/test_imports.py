"""Import-sanity check for the shared config loader consumed by both tiers."""


def test_portal_main_imports():
    import portal.main  # noqa: F401
