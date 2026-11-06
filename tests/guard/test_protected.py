import pytest

from earned_autonomy.guard.protected import GuardConfig, guard, protected

CFG = GuardConfig.from_dict({
    "fallbacks": {"trash": "archive"},
    "categories": [
        {"name": "known contact", "addresses": ["Friend@Mail.Example.com"]},
        {"name": "bank or creditor", "domains": ["examplebank.test"]},
        {"name": "government", "suffixes": [".gov"]},
    ],
})


def test_a_known_contact_is_protected_case_insensitively():
    assert protected("friend@mail.example.com", CFG) == "known contact"


@pytest.mark.parametrize("sender", ["alerts@examplebank.test", "x@e.examplebank.test", "Bank <no-reply@examplebank.test>"])
def test_a_protected_domain_and_its_subdomains(sender):
    assert protected(sender, CFG) == "bank or creditor"


def test_a_suffix_category():
    assert protected("info@city.example.gov", CFG) == "government"


def test_a_lookalike_domain_is_not_protected():
    assert protected("x@notexamplebank.test", CFG) is None


def test_an_unparseable_target_fails_closed():
    assert protected("not an address", CFG) == "unparseable target"


def test_an_approved_trash_on_a_protected_target_becomes_an_archive():
    g = guard("trash", "alerts@examplebank.test", CFG)
    assert g.action == "archive" and "bank or creditor" in g.reason


def test_an_ordinary_target_runs_as_approved():
    assert guard("trash", "deals@shop.example.com", CFG).action == "trash"


def test_actions_the_config_does_not_guard_pass_through():
    assert guard("label", "alerts@examplebank.test", CFG).action == "label"
