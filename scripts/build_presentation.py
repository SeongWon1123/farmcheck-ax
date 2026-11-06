# Copyright (c) 2026 FarmCheck contributors.
"""Build presentation/deck.pptx from owned text and drawn shapes only.

Run: uv run --isolated --no-project --with "python-pptx>=1,<2" python scripts/build_presentation.py
PDF: scripts/build_presentation.py --pdf exports through installed PowerPoint (Windows COM).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Emu, Inches, Pt

if TYPE_CHECKING:
    from pptx.shapes.autoshape import Shape

OUT_DIR: Final = Path(__file__).resolve().parent.parent / "presentation"
FONT: Final = "Malgun Gothic"
INK: Final = RGBColor(0x1F, 0x2A, 0x24)
MUTED: Final = RGBColor(0x5B, 0x66, 0x60)
GREEN: Final = RGBColor(0x2F, 0x6B, 0x3F)
WASH: Final = RGBColor(0xEC, 0xF3, 0xEE)
AMBER: Final = RGBColor(0x9A, 0x5B, 0x00)
TAG_COLORS: Final = {"관측": GREEN, "계획": AMBER, "주입": RGBColor(0x5A, 0x4F, 0x9C)}


@dataclass(frozen=True, slots=True)
class Slide:
    """One slide's owned copy and optional process diagram."""

    title: str
    bullets: tuple[str, ...]
    boxes: tuple[str, ...] = ()
    notes: str = ""


SLIDES: tuple[Slide, ...] = (
    Slide("팜체크: 다시 볼 녹차 기록과 남은 질문을 다음 사람에게",
          ("확인할 기록을 찾고, 원본 근거와 미해결 질문을 인수인계합니다",
           "진단·처방·원인 확정·원본 자동 수정은 하지 않습니다",
           "[관측] 실제 확인 · [계획] 아직 안 함 · [주입] 시연용 합성 장면"),
           notes="표기 규칙부터 말한다. 이후 모든 숫자에 태그가 붙는다."),
    Slide("문제: 기록은 있는데 넘겨받을 근거가 없다",
          ("이상한 값을 봐도 원문, 단위, 시각을 다시 찾느라 시간이 듭니다",
           "보류한 이유와 다음 할 일이 교대 때 사라집니다",
           "'봤음'과 '해결됨'이 같은 표시로 섞입니다"),
          ("발견", "근거 확인", "판단", "재검사", "인수인계")),
    Slide("원천 데이터에서 직접 확인한 것",
          ("[관측] 녹차 사진·JSON 3,872쌍, 원본 ZIP은 바꾸지 않고 해시로 고정",
           "[관측] 실제 사진은 모두 380x380, JSON 선언 해상도와 다름",
           "그래서 확대·좌표 표시·잎 크기 측정은 하지 않습니다",
           "[관측] 148개 기록은 환경 시각이 사진보다 1~10초 늦음, 오류로 보지 않음")),
    Slide("33개와 24개를 읽는 법",
          ("[관측] pH > 14 사진 기록 33개",
           "[관측] 측정 시각 + pH, EC, CO2, 온도로 묶으면 후보 묶음 24개",
           "식물 문제 33건도, 센서 고장 24회도 아닙니다",
           "묶음은 보기용: 합치지 않고, 처리가 다른 기록으로 번지지 않음"),
          ("사진 기록 33", "후보 묶음 24", "결론 0"),
          notes="가장 오해받기 쉬운 장표. 단위가 다른 두 숫자임을 강조."),
    Slide("작업 축: 검토와 해소는 따로 움직인다",
          ("검토 상태: 미검토 / 검토함 / 재검토 필요",
           "해소 상태: 열림 / 보류 / 해소",
           "보류는 이유와 다음 조치 필수, 해소는 최신 재검사와 근거 필수",
           "수정은 덧씌우기, 이전 검토·해소 무효, 보류 질문은 이력으로 남음"),
          ("검토함 + 보류 = 정상", "근거 없는 해소 = 거부")),
    Slide("같은 출처는 새 근거가 아니다",
          ("SQL, 캡션, 에이전트가 같은 JSON을 다시 말해도 source_group_id가 같습니다",
           "출처: record_id, ZIP 경로, 원본 해시, 필드 경로, origin",
           "origin: actual / injected / recorded / synthetic",
           "캡션의 dS/m 단위는 '출처 선언'이지 독립 증거가 아님")),
    Slide("기존 작물·주차 모델: 연결 확인 (정확도 아님)",
          ("[관측] 2026-11-06 녹차 5장, 서로 다른 주차 0~4, 모두 HTTP 200",
           "[관측] 반환 작물 녹차, 반환 주차가 원천 주차와 같음",
           "작은 편의 표본, 학습 중복 미확인, 신뢰도는 보정값 아님",
           "보낸 것은 원본 바이트 한 장, 이름·라벨·캡션은 안 보냄"),
          ("5 요청", "5 HTTP 200", "정확도 주장 없음"),
          notes="docs/model-smoke.md 의 표를 그대로 인용. 일치를 정확도로 말하지 않는다."),
    Slide("모델이 없어도 일은 끝난다",
          ("모델 결과는 '다시 볼 이유'이며 사람의 검토·해소를 자동 완료하지 않습니다",
           "요청 뒤 기록이 바뀌면 결과는 '오래됨'으로 남음",
           "실패·지연 때도 수동 검토, 보류, 수정, 인수인계 가능",
           "[주입] 공개 시연의 모델 실패·불일치 장면은 합성")),
    Slide("인수인계 스냅샷",
          ("서버가 작업공간 버전을 다시 확인, 바뀌었으면 409로 거부",
           "바이트와 SHA-256 고정, 나중에 내려받아도 같은 파일",
           "열림·보류·재검사 필요 질문을 모두 싣고, 미해결을 숨기지 않음",
           "JSON / CSV / HTML, CSV 수식 시작 문자 무력화")),
    Slide("개인정보와 공개 모드",
          ("공개 서버는 합성 기록과 직접 그린 SVG만 사용, '합성 시연 데이터' 표시",
           "클라이언트가 실제 데이터 모드나 API 주소를 바꿀 수 없음",
           "원천 ZIP, 실제 사진, 비공개 응답 보고서, DB, 키는 공개하지 않음",
           "[관측] Ubuntu 공개 주소에서 합성 모드와 고정본 다운로드 검증",
           "devopen.jntp-ax.or.kr/i/SpPClk4QcdJwast1/")),
    Slide("AgentStream: 내일 실연동",
          ("[관측] 오늘 상태 disabled_by_owner, 질의는 503",
           "지금 되는 것: 읽기 전용 버전 문맥 내보내기, 출력 검증 경계",
           "검증: 모르는 ID·근거 참조, 변경 필드, 오래된 스냅샷 거부",
           "에이전트 답변 예시와 성능 주장은 없습니다")),
    Slide("근거의 범위와 다음 실험",
          ("시스템 동작은 확인했지만 현장 효과는 재지 않았습니다",
           "[계획] 수동 / 규칙 / 에이전트 3조건 참가자 내 실험",
           "지표: 누락률, 질문 전달률, 상태 혼동, 시간, 체감 부담",
           "[관측] 시스템 회귀66개 통과; 독립 리뷰·실행 증거는 별도 기록"),
          ("A 수동", "B 규칙", "C 에이전트"),
          notes="docs/evaluation-protocol.md 참조. 결과는 아직 없다고 명확히."),
)


def _text(
    frame_shape: Shape, text: str, size: int, color: RGBColor, *, bold: bool = False,
) -> None:
    """Apply readable Korean typography to an owned text-bearing shape."""
    tf = frame_shape.text_frame
    tf.word_wrap = True
    run = tf.paragraphs[0].add_run()
    run.text = text
    run.font.name, run.font.size, run.font.bold = FONT, Pt(size), bold
    run.font.color.rgb = color


def _bullet_color(line: str) -> RGBColor:
    for tag, color in TAG_COLORS.items():
        if line.startswith(f"[{tag}]"):
            return color
    return INK


def build(out: Path) -> Path:
    """Generate the complete deck with no source photographs or external assets."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    for index, spec in enumerate(SLIDES, start=1):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        band = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Emu(0), Emu(0), Inches(13.333), Inches(0.18),
        )
        band.fill.solid()
        band.fill.fore_color.rgb = GREEN
        band.line.fill.background()
        _text(slide.shapes.add_textbox(Inches(0.7), Inches(0.5), Inches(12), Inches(1)),
              spec.title, 30, INK, bold=True)
        body = slide.shapes.add_textbox(Inches(0.7), Inches(1.7), Inches(12), Inches(3.4))
        tf = body.text_frame
        tf.word_wrap = True
        for i, line in enumerate(spec.bullets):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.space_after = Pt(10)
            run = para.add_run()
            run.text = f"•  {line}"
            run.font.name, run.font.size = FONT, Pt(20)
            run.font.color.rgb = _bullet_color(line)
        if spec.boxes:
            gap = Inches(0.25)
            width = Emu(int((Inches(12) - gap * (len(spec.boxes) - 1)) / len(spec.boxes)))
            for j, label in enumerate(spec.boxes):
                left = Emu(Inches(0.7) + j * (width + gap))
                box = slide.shapes.add_shape(
                    MSO_SHAPE.ROUNDED_RECTANGLE, left, Inches(5.5), width, Inches(1.1),
                )
                box.fill.solid()
                box.fill.fore_color.rgb = WASH
                box.line.fill.background()
                _text(box, label, 20, GREEN, bold=True)
        _text(slide.shapes.add_textbox(Inches(11.6), Inches(6.9), Inches(1.5), Inches(0.4)),
              f"{index} / {len(SLIDES)}", 12, MUTED)
        notes_frame = slide.notes_slide.notes_text_frame
        if spec.notes and notes_frame is not None:
            notes_frame.text = spec.notes
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return out


def export_pdf() -> Path:
    """Export the fixed owned deck through installed Windows PowerPoint."""
    _ = subprocess.run(
        [
            "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            "-NoProfile", "-Command",
            """
            $root=Join-Path (Get-Location) 'presentation'
            $p=New-Object -ComObject PowerPoint.Application
            $d=$p.Presentations.Open((Join-Path $root 'deck.pptx'),$true,$false,$false)
            $d.SaveAs((Join-Path $root 'deck.pdf'),32)
            $d.Export((Join-Path $root 'render'),'PNG',1600,900)
            $d.Close()
            if ($p.Presentations.Count -eq 0) { $p.Quit() }
            """,
        ],
        cwd=OUT_DIR.parent, check=True, timeout=180,
    )
    return OUT_DIR / "deck.pdf"


class PresentationArguments(argparse.Namespace):
    """Parse the one optional build flag without dynamic downstream values."""

    pdf: bool = False


def main() -> None:
    """Build slide artifacts; PDF export is an explicit Windows-only option."""
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--pdf", action="store_true")
    args = PresentationArguments()
    _ = parser.parse_args(namespace=args)
    deck = build(OUT_DIR / "deck.pptx")
    _ = sys.stdout.write(str(deck) + "\n")
    if args.pdf:
        _ = sys.stdout.write(str(export_pdf()) + "\n")


if __name__ == "__main__":
    main()
