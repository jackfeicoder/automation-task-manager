"""Page ownership and connection validation without a real user browser."""
import json
from contextlib import contextmanager

import pytest

from tasks.edge_rewards import desktop_browser as desktop


class Page:
    def __init__(self, parent=None):
        self.parent = parent
        self.closed = False
        self.routes = []
        self.viewport = None

    def set_viewport_size(self, size):
        self.viewport = size

    def opener(self):
        return self.parent

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True

    def route(self, *args):
        self.routes.append(args)


class Context:
    def __init__(self):
        self.pages = [Page()]
        self.callbacks = []

    def on(self, event, callback):
        self.callbacks.append(callback)

    def remove_listener(self, event, callback):
        self.callbacks.remove(callback)

    def new_page(self, parent=None):
        page = Page(parent)
        self.pages.append(page)
        for callback in self.callbacks:
            callback(page)
        return page

    def close(self):
        pytest.fail('Do not close the original browser context')


def test_cleanup_closes_task_pages_only_even_when_user_opens_tabs():
    context = Context()
    original = context.pages[0]
    task = desktop.TaskPages(context)
    work = task.new_page()
    popup = context.new_page(parent=work)
    nested = context.new_page(parent=popup)
    user_new_tab = context.new_page()
    user_popup = context.new_page(parent=original)
    assert task.pages == [work, popup, nested]
    assert work.viewport == popup.viewport == nested.viewport == {'width': 1280, 'height': 900}
    assert original.viewport is None and user_new_tab.viewport is None and user_popup.viewport is None
    task.close()
    assert work.closed and popup.closed and nested.closed
    assert not original.closed and not user_new_tab.closed and not user_popup.closed
    assert not context.callbacks


def test_routes_are_limited_to_owned_pages_and_popups():
    context = Context()
    original = context.pages[0]
    task = desktop.TaskPages(context)
    handler = lambda route: None
    task.route('**/*', handler)
    work = task.new_page()
    popup = context.new_page(parent=work)
    user_tab = context.new_page()
    assert work.routes == popup.routes == [('**/*', handler)]
    assert original.routes == user_tab.routes == []
    task.close()


@pytest.mark.parametrize('endpoint', ['https://127.0.0.1:9222', 'http://example.org:9222',
                                     'http://127.0.0.1:0', 'http://127.0.0.1:invalid',
                                     'http://127.0.0.1:9222/other', 'http://[broken'])
def test_endpoint_is_local_only(endpoint):
    with pytest.raises(ValueError):
        desktop.endpoint_url({'REWARDS_EDGE_ENDPOINT': endpoint})


def test_default_endpoint_and_custom_local_port():
    assert desktop.endpoint_url({}) == 'http://127.0.0.1:9222'
    assert desktop.endpoint_url({'REWARDS_EDGE_ENDPOINT': 'http://localhost:9333/'}) == 'http://localhost:9333'


@pytest.mark.parametrize('agent,valid', [('Mozilla/5.0 Edg/140.0', True), ('Mozilla/5.0 Chrome/140.0', False)])
def test_only_edge_is_accepted_and_local_probe_does_not_use_proxy(monkeypatch, agent, valid):
    @contextmanager
    def response():
        class Reply:
            def read(self, size):
                return json.dumps({'User-Agent': agent, 'Browser': 'Chrome/140.0',
                                   'webSocketDebuggerUrl': 'ws://127.0.0.1:9222/devtools/browser/example'}).encode()
        yield Reply()

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == 'http://127.0.0.1:9222/json/version'
            return response()

    def opener(handler):
        assert handler.proxies == {}
        return Opener()
    monkeypatch.setattr(desktop, 'build_opener', opener)
    if valid:
        assert desktop.probe_desktop_edge('http://127.0.0.1:9222') == 'ws://127.0.0.1:9222/devtools/browser/example'
    else:
        with pytest.raises(ValueError, match='不是 Microsoft Edge'):
            desktop.probe_desktop_edge('http://127.0.0.1:9222')


def test_non_object_probe_response_has_actionable_message(monkeypatch):
    @contextmanager
    def response():
        class Reply:
            def read(self, size):
                return b'[]'
        yield Reply()

    class Opener:
        def open(self, request, timeout):
            return response()

    monkeypatch.setattr(desktop, 'build_opener', lambda handler: Opener())
    with pytest.raises(ValueError, match='start_edge.ps1'):
        desktop.probe_desktop_edge('http://127.0.0.1:9222')
