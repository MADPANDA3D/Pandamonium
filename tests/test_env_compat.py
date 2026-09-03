from src.env_compat import apply_legacy_env_aliases


def test_pandamonium_environment_wins_without_breaking_legacy_names():
    environ = {
        "PANDAMONIUM_DATA_DIR": "/new",
        "ODYSSEUS_DATA_DIR": "/old",
        "ODYSSEUS_ADMIN_USER": "legacy-admin",
    }

    apply_legacy_env_aliases(environ)

    assert environ["ODYSSEUS_DATA_DIR"] == "/new"
    assert environ["ODYSSEUS_ADMIN_USER"] == "legacy-admin"
