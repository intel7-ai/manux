"""Standalone apps have their own environment and test root."""


def pytest_ignore_collect(collection_path, config):
    # Run that app's tests from inside its folder. Its `src` package is separate
    # from the main application's `src` package and must not shadow it.
    return collection_path.name == 'fist-mouse-lab'
