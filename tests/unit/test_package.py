import switchboard


def test_package_exposes_version() -> None:
    assert switchboard.__version__ == "0.1.0"
