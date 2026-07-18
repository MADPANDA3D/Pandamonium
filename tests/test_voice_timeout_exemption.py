import ast
from pathlib import Path


def _timeout_matcher():
    source = (Path(__file__).parents[1] / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_is_timeout_exempt"
    )
    namespace = {"_TIMEOUT_EXEMPT_PREFIXES": ()}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "app.py", "exec"), namespace)
    return namespace["_is_timeout_exempt"]


def test_only_voice_turn_audio_is_timeout_exempt():
    is_timeout_exempt = _timeout_matcher()
    assert is_timeout_exempt("/api/voice/sessions/session-1/turns/turn-1/audio")
    assert not is_timeout_exempt("/api/voice/sessions/session-1/respond")
    assert not is_timeout_exempt("/api/voice/sessions/session-1/turns/turn-1/playback")
