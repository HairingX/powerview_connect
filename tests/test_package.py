"""The package as it is installed: what a type checker and a user of it see."""
import importlib.metadata
import importlib.resources
import dataclasses
import re

import powerview_connect


def test_it_ships_its_types() -> None:
    """PEP 561: without py.typed, a type checker treats every import of the package as untyped."""
    assert importlib.resources.files(powerview_connect).joinpath("py.typed").is_file()


def test_its_version_is_the_one_it_was_installed_as() -> None:
    assert powerview_connect.__version__ == importlib.metadata.version("powerview_connect")


def test_every_link_in_its_description_works_where_the_description_is_shown() -> None:
    """PyPI shows the README without the repository's files, so a relative link there is dead."""
    description = importlib.metadata.metadata("powerview_connect").json["description"]
    assert isinstance(description, str) and description
    links = re.findall(r"\]\(([^)]+)\)", description)
    assert links and all(link.startswith(("https://", "#")) for link in links), links


def test_everything_it_names_is_there() -> None:
    import powerview_connect.testing

    for package in (powerview_connect, powerview_connect.testing):
        for name in package.__all__:
            assert hasattr(package, name), name


def test_every_data_class_it_names_takes_its_fields_by_name() -> None:
    """A field can then be added anywhere without breaking a caller, or a `match` on it."""
    import powerview_connect.testing

    for package in (powerview_connect, powerview_connect.testing):
        for name in package.__all__:
            found: object = getattr(package, name)
            if isinstance(found, type) and dataclasses.is_dataclass(found):
                assert all(field.kw_only for field in dataclasses.fields(found)), name
                assert getattr(found, "__match_args__", ()) == (), name


def test_its_only_dependency_is_aiohttp() -> None:
    requirements = importlib.metadata.requires("powerview_connect") or []
    assert [re.split(r"[<>=!~ ;\[]", r)[0] for r in requirements] == ["aiohttp"]
