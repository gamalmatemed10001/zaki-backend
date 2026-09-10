"""Router is a pure function — no API keys needed to test it."""

from zaki.router import Route, choose_route


def test_short_simple_request_routes_to_gemini():
    assert choose_route("عامل ايه النهاردة؟") is Route.GEMINI


def test_complexity_cue_routes_to_claude():
    assert choose_route("حلل لي الوضع ده") is Route.CLAUDE


def test_another_complexity_cue():
    assert choose_route("قارن بين الخيارين دول") is Route.CLAUDE


def test_long_input_escalates_regardless_of_cues():
    long_text = "كلام عادي من غير كلمات معقدة " * 20
    assert len(long_text) > 280
    assert choose_route(long_text) is Route.CLAUDE


def test_short_input_respects_custom_threshold():
    text = "نص متوسط الطول شوية"
    assert choose_route(text, escalation_length=5) is Route.CLAUDE
    assert choose_route(text, escalation_length=500) is Route.GEMINI
