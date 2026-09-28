from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from format_reply import format_latest, format_projects, format_reply
from service import (
    LatestFolder,
    ProjectInventory,
    ProjectRow,
    detect_inventory_intent,
)


def test_detect_projects_intent() -> None:
    assert detect_inventory_intent("몇개의 프로젝트가 있어?") == "projects"
    assert detect_inventory_intent("전체 프로젝트 수") == "projects"
    assert detect_inventory_intent("프로젝트 목록") == "projects"


def test_detect_latest_intent() -> None:
    assert detect_inventory_intent("제일 최신 작업 폴더") == "latest"
    assert detect_inventory_intent("최근 프로젝트") == "latest"


def test_detect_no_intent() -> None:
    assert detect_inventory_intent("Manufacturing-X 목표가 뭐야?") is None


def test_format_projects() -> None:
    inv = ProjectInventory(
        projects=[
            ProjectRow(
                project_id="Manufacturing-X",
                title="한국형 MX",
                document_count=8,
                schedule_count=2,
                last_activity="2026-09-28",
            )
        ],
        total_documents=8,
    )
    text = format_projects(inv)[0]
    assert "프로젝트 1개" in text
    assert "Manufacturing-X" in text
    assert "문서 8건" in text


def test_format_latest() -> None:
    folder = LatestFolder(
        project_id="금형DX",
        title="금형DX",
        last_activity="2026-09-20",
        document_count=3,
        latest_document_title="워크샵 발표",
    )
    text = format_latest(folder)[0]
    assert "금형DX" in text
    assert "워크샵 발표" in text


def test_format_reply_hides_citations_on_refusal() -> None:
    result = SimpleNamespace(
        answer="메모리에 근거가 없어 답할 수 없습니다",
        refused=False,
        citations=[
            SimpleNamespace(
                filename="noise.docx",
                location="p.1",
                document_role="project_document",
                document_id="d1",
            )
        ],
    )
    text = format_reply(result)[0]
    assert "출처" not in text
    assert "noise.docx" not in text
