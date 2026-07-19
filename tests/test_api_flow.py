from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import app


def auth_header(token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}'}


def test_admin_and_user_api_flow() -> None:
    with TestClient(app) as client:
        login = client.post('/api/auth/admin/login', json={'username': 'admin', 'password': 'Admin_Test_12345'})
        assert login.status_code == 200, login.text
        admin_token = login.json()['access_token']
        admin_headers = auth_header(admin_token)

        source_response = client.post('/api/admin/sources', headers=admin_headers, json={'name': 'API manba'})
        assert source_response.status_code == 201, source_response.text
        source_id = source_response.json()['id']

        question_response = client.post(
            '/api/admin/questions',
            headers=admin_headers,
            json={
                'source_id': source_id,
                'question_text': 'API testi savoli?',
                'answers': [
                    {'text': 'To‘g‘ri', 'correct': True},
                    {'text': 'Noto‘g‘ri', 'correct': False},
                ],
            },
        )
        assert question_response.status_code == 201, question_response.text

        test_response = client.post(
            '/api/admin/tests',
            headers=admin_headers,
            json={
                'name': 'API testi',
                'time_limit_minutes': 10,
                'is_active': True,
                'rules': [{'source_id': source_id, 'question_count': 1}],
            },
        )
        assert test_response.status_code == 201, test_response.text
        test_id = test_response.json()['id']

        dev_login = client.post('/api/auth/dev', json={'full_name': 'API user'})
        assert dev_login.status_code == 200, dev_login.text
        user_headers = auth_header(dev_login.json()['access_token'])

        tests = client.get('/api/tests', headers=user_headers)
        assert tests.status_code == 200, tests.text
        assert any(item['id'] == test_id for item in tests.json())

        attempt_response = client.post('/api/attempts', headers=user_headers, json={'test_id': test_id})
        assert attempt_response.status_code == 201, attempt_response.text
        attempt = attempt_response.json()
        question = attempt['questions'][0]
        assert all('correct' not in answer for answer in question['answers'])

        admin_question = question_response.json()
        correct_answer_id = next(answer['id'] for answer in admin_question['answers'] if answer['correct'])
        answer_response = client.post(
            f"/api/attempts/{attempt['id']}/answer",
            headers=user_headers,
            json={'question_id': question['question_id'], 'answer_id': correct_answer_id},
        )
        assert answer_response.status_code == 200, answer_response.text
        assert answer_response.json()['is_correct'] is True

        finish = client.post(f"/api/attempts/{attempt['id']}/finish", headers=user_headers)
        assert finish.status_code == 200, finish.text
        assert finish.json()['percentage'] == 100
