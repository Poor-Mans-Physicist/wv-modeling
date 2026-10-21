"""Deck-menu designs never offer a card whose stat the rework bans (stages.REWORK_BANNED_CARDS): the fixed-layout path
filters them in context.init_deck, the menu path in deck_rework.build_designs."""
from types import SimpleNamespace

from model import deck, deck_rework, stages


def test_menu_designs_offer_no_banned_cards():
    reg = deck.card_registry()
    menus = deck_rework.load_menus(None)
    assert menus, "no deck menus to check"
    for name, menu in menus.items():
        ctx = SimpleNamespace(stage={"card_tier": 5}, card_registry=reg, stage_name=name)
        for d in deck_rework.build_designs(ctx, menu):
            offered = {o["attr"] for opts in d["opts"] for o in opts}
            bad = offered & stages.REWORK_BANNED_CARDS
            assert not bad, f"{name} {d['label']}: offers banned stats {sorted(bad)}"
