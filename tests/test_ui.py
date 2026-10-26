from agents import ui


def test_print_cost_starts_on_a_new_line(monkeypatch):
    calls = []
    monkeypatch.setattr(ui.console, "print", lambda *args, **kwargs: calls.append((args, kwargs)))

    ui.print_cost(100, 20)

    assert calls[0] == ((), {})
    assert isinstance(calls[1][0][0], ui.Panel)
    assert calls[1][0][0].title == "费用"
