from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.app.importers import parse_db, parse_txt


def test_txt_parser_supports_marked_answers() -> None:
    content = '''1. Poytaxt qaysi shahar?\nA) Samarqand\n*B) Toshkent\nC) Buxoro\n\n2. 2 + 2?\n+4\n3\n5\n'''.encode()
    items = parse_txt(content)
    assert len(items) == 2
    assert all(item.valid for item in items)
    assert items[0].question == 'Poytaxt qaysi shahar?'
    assert next(answer.text for answer in items[0].answers if answer.correct) == 'Toshkent'


def test_old_sqlite_without_position_column_is_supported(tmp_path: Path) -> None:
    db_path = tmp_path / 'old.db'
    connection = sqlite3.connect(db_path)
    connection.executescript('''
        CREATE TABLE sources (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE source_questions (id INTEGER PRIMARY KEY, source_id INTEGER, question_text TEXT, question_type TEXT);
        CREATE TABLE source_answers (id INTEGER PRIMARY KEY, question_id INTEGER, answer_text TEXT, is_correct INTEGER);
        INSERT INTO sources VALUES (1, 'Eski manba');
        INSERT INTO source_questions VALUES (10, 1, 'Eski savol?', 'multiple-choice');
        INSERT INTO source_answers VALUES (1, 10, 'To‘g‘ri', 1);
        INSERT INTO source_answers VALUES (2, 10, 'Noto‘g‘ri', 0);
    ''')
    connection.commit()
    connection.close()

    items = parse_db(db_path.read_bytes(), mode='full')
    assert len(items) == 1
    assert items[0].valid is True
    assert items[0].source_name == 'Eski manba'
