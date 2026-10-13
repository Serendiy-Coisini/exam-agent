"""Model configuration API tests; no external model calls or real secrets."""
import asyncio
import json
import unittest
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.core.config import settings
from app.services import model_settings_service as service
from app.services import llm_service
from app.schemas.model_settings_schema import ModelSettingsPayload

from tests import test_regressions as fixtures
request = fixtures.request


class ModelSettingsTests(unittest.TestCase):
    def setUp(self):
        fixtures.AuditRegressions.setUp(self)
        for p in [patch.object(service, "MODEL_SETTINGS_FILE", Path(self.temp.name) / "model.json"),
                  patch.object(settings, "LLM_BASE_URL", "https://model.example/v1")]:
            p.start()
            self.patches.append(p)

    def payload(self, **values):
        return {"base_url": "https://model.example/v1", "model_name": "fiction-model",
                "api_key": "fiction-key", **values}
    tearDown = fixtures.AuditRegressions.tearDown

    def test_model_settings_status_is_available_without_returning_key(self):
        status, data = asyncio.run(request('GET', '/api/v1/settings/model'))
        self.assertEqual(status, 200)
        self.assertFalse(data['configured'])
        self.assertFalse(data['api_key_set'])
        self.assertNotIn('api_key', data)

    def test_formal_auth_protects_model_settings(self):
        from app.core.config import settings
        settings.AUTH_ENABLED = True
        for method, path in [('GET', '/api/v1/settings/model'),
                             ('PUT', '/api/v1/settings/model'),
                             ('POST', '/api/v1/settings/model/test')]:
            with self.subTest(method=method):
                self.assertEqual(asyncio.run(request(method, path))[0], 401)


    def test_save_preserves_blank_key_and_loads_after_restart(self):
        status, data = asyncio.run(request('PUT', '/api/v1/settings/model', self.payload()))
        self.assertEqual(status, 200)
        self.assertTrue(data['configured'])
        self.assertNotIn('fiction-key', json.dumps(data))
        self.assertEqual(settings.LLM_MODEL_NAME, 'fiction-model')
        self.assertEqual(settings.LLM_API_KEY, 'fiction-key')
        asyncio.run(request('PUT', '/api/v1/settings/model', self.payload(api_key='', model_name='next-model')))
        self.assertEqual(settings.LLM_API_KEY, 'fiction-key')
        settings.LLM_API_KEY = ''
        settings.LLM_MODEL_NAME = ''
        service.load_model_settings()
        self.assertEqual(settings.LLM_MODEL_NAME, 'next-model')
        self.assertEqual(settings.LLM_API_KEY, 'fiction-key')

    def test_clear_key_persists_and_disables_model(self):
        asyncio.run(request('PUT', '/api/v1/settings/model', self.payload()))
        status, data = asyncio.run(request('PUT', '/api/v1/settings/model', self.payload(api_key='', clear_api_key=True)))
        self.assertEqual(status, 200)
        self.assertFalse(data['configured'])
        settings.LLM_API_KEY = 'environment-key'
        service.load_model_settings()
        self.assertEqual(settings.LLM_API_KEY, '')

    def test_saved_secret_is_not_sent_to_a_changed_endpoint(self):
        asyncio.run(request('PUT', '/api/v1/settings/model', self.payload()))
        for method, suffix in [('PUT', ''), ('POST', '/test')]:
            with self.subTest(method=method):
                status, data = asyncio.run(request(method, '/api/v1/settings/model'+suffix,
                    self.payload(api_key='', base_url='https://other.example/v1')))
                self.assertEqual(status, 422)
                self.assertNotIn('fiction-key', json.dumps(data))
        self.assertEqual(settings.LLM_BASE_URL, 'https://model.example/v1')

    def test_invalid_inputs_never_echo_secret(self):
        for values in [{'base_url':'javascript:bad'}, {'api_key':'fiction-secret invalid'},
                       {'clear_api_key':True}, {'extra':'field'}, {'model_name':' '}]:
            with self.subTest(values=list(values)):
                status, data = asyncio.run(request('PUT', '/api/v1/settings/model', self.payload(**values)))
                self.assertEqual(status, 422)
                self.assertNotIn('fiction-secret', json.dumps(data))
                self.assertNotIn('fiction-key', json.dumps(data))
                self.assertFalse(service.MODEL_SETTINGS_FILE.exists())

    def test_storage_failure_does_not_change_active_config(self):
        with patch.object(service.os, 'replace', side_effect=PermissionError('fiction-key')):
            status, data = asyncio.run(request('PUT', '/api/v1/settings/model', self.payload()))
        self.assertEqual(status, 500)
        self.assertNotIn('fiction-key', json.dumps(data))
        self.assertEqual(settings.LLM_API_KEY, '')
        self.assertFalse(service.MODEL_SETTINGS_FILE.exists())
        self.assertFalse(list(service.MODEL_SETTINGS_FILE.parent.glob('.model-settings-*')))

    def test_connection_uses_draft_without_saving_or_sending_profile(self):
        fake = MagicMock()
        fake.__enter__.return_value = fake
        fake.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='OK'))])
        with patch('openai.OpenAI', return_value=fake) as factory:
            status, data = asyncio.run(request('POST', '/api/v1/settings/model/test', self.payload()))
        self.assertEqual(status, 200)
        self.assertTrue(data['ok'])
        self.assertFalse(service.MODEL_SETTINGS_FILE.exists())
        self.assertEqual(settings.LLM_API_KEY, '')
        self.assertEqual(factory.call_args.kwargs['max_retries'], 0)
        self.assertLessEqual(factory.call_args.kwargs['timeout'], 10)
        self.assertEqual(fake.chat.completions.create.call_args.kwargs['messages'],
                         [{'role':'user','content':'请只回复 OK。'}])

    def test_connection_error_is_safe(self):
        fake = MagicMock()
        fake.__enter__.return_value = fake
        fake.chat.completions.create.side_effect = RuntimeError('fiction-key sensitive provider body')
        with patch('openai.OpenAI', return_value=fake):
            status, data = asyncio.run(request('POST', '/api/v1/settings/model/test', self.payload()))
        self.assertEqual(status, 200)
        self.assertFalse(data['ok'])
        self.assertNotIn('fiction-key', json.dumps(data))
        self.assertNotIn('sensitive provider body', json.dumps(data))

    def test_admin_role_required_in_formal_auth(self):
        settings.AUTH_ENABLED = True
        from app.db import repositories
        from app.services.auth_service import create_access_token
        user = repositories.upsert_dev_user('ordinary')
        status, _ = asyncio.run(request('GET', '/api/v1/settings/model', token=create_access_token(user)))
        self.assertEqual(status, 403)
        user['role'] = 'admin'
        from app.db.database import connection
        with connection() as db:
            db.execute("UPDATE users SET role='admin' WHERE id=?", (user['id'],))
        status, _ = asyncio.run(request('GET', '/api/v1/settings/model', token=create_access_token(user)))
        self.assertEqual(status, 200)

    def test_remote_or_cross_origin_requests_are_denied(self):
        real_app = fixtures.app
        for host, peer, origin in [('127.0.0.1:18100','192.0.2.1',None),
                                   ('evil.example','127.0.0.1',None),
                                   ('127.0.0.1:18100','127.0.0.1','https://evil.example')]:
            async def wrapped(scope, receive, send):
                scope = {**scope, 'client':(peer,10), 'headers':list(scope['headers'])}
                scope['headers'].append((b'host',host.encode()))
                if origin:
                    scope['headers'].append((b'origin',origin.encode()))
                await real_app(scope,receive,send)
            with patch.object(fixtures,'app',wrapped):
                status, _ = asyncio.run(request('PUT','/api/v1/settings/model',self.payload()))
            self.assertEqual(status,403)
        self.assertFalse(service.MODEL_SETTINGS_FILE.exists())

    def test_analysis_uses_one_configuration_snapshot(self):
        asyncio.run(request('PUT', '/api/v1/settings/model', self.payload()))
        snapshot = service.get_model_configuration()
        settings.LLM_BASE_URL = 'https://new.example/v1'
        settings.LLM_MODEL_NAME = 'changed'
        settings.LLM_API_KEY = 'changed-key'
        factory = MagicMock()
        llm_service._build_client(factory, snapshot)
        self.assertEqual(factory.call_args.kwargs['base_url'], 'https://model.example/v1')
        self.assertEqual(factory.call_args.kwargs['api_key'], 'fiction-key')


    def test_real_sdk_connects_to_loopback_provider_and_analysis_uses_saved_model(self):
        seen = []
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                seen.append((self.path, self.headers.get('Authorization'), body))
                response = json.dumps({'id':'fiction-completion','object':'chat.completion',
                    'created':1,'model':body['model'], 'choices':[{'index':0,
                    'message':{'role':'assistant','content':'OK'}, 'finish_reason':'stop'}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(response)))
                self.end_headers()
                self.wfile.write(response)
        server = ThreadingHTTPServer(('127.0.0.1',0), Provider)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            payload = self.payload(base_url=f'http://127.0.0.1:{server.server_port}/v1')
            with patch.dict('os.environ', {'NO_PROXY':'127.0.0.1,localhost'}):
                status,data = asyncio.run(request('POST','/api/v1/settings/model/test',payload))
                self.assertEqual(status,200)
                self.assertTrue(data['ok'])
                self.assertEqual(asyncio.run(request('PUT','/api/v1/settings/model',payload))[0],200)
                result = llm_service.generate_llm_chat('fiction system',[{'role':'user','content':'fiction question'}], 'fallback')
            self.assertTrue(result['llm_used'])
            self.assertEqual(result['model'],'fiction-model')
            self.assertEqual(result['summary'],'OK')
            self.assertEqual(len(seen),2)
            self.assertTrue(all(item[0]=='/v1/chat/completions' for item in seen))
            self.assertTrue(all(item[1]=='Bearer fiction-key' for item in seen))
            self.assertTrue(all(item[2]['model']=='fiction-model' for item in seen))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
