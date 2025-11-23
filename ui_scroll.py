#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import urwid
import sys, locale, logging
from typing import Optional, Tuple
import time
import os  # [추가]
import atexit


def _detect_encoding() -> str:
    return (sys.stdout.encoding or locale.getpreferredencoding(False) or "ascii").lower()

_USE_UTF8 = ("utf" in _detect_encoding())
TRACK_CHAR = "│" if _USE_UTF8 else "|"
THUMB_CHAR = "█" if _USE_UTF8 else "@"

# =========================[추가] UI 전용 로거 + 안전한 파일쓰기 헬퍼 =========================
_UI_LOGGER = None

# [추가] xterm 마우스 모드 강제 토글
def _set_xterm_mouse_tracking(enable: bool):
    """
    xterm mouse tracking 모드 강제 전환:
      - 1006: SGR 좌표 포맷
      - 1002: 버튼 누른 상태에서 이동 보고
      - 1003: Any-event(모든 이동 보고)
    """
    try:
        seq = []
        seq.append("\x1b[?1006h" if enable else "\x1b[?1006l")
        seq.append("\x1b[?1002h" if enable else "\x1b[?1002l")
        seq.append("\x1b[?1003h" if enable else "\x1b[?1003l")
        sys.stdout.write("".join(seq))
        sys.stdout.flush()
        UI_INFO(f"[MOUSEMODE] any-event={'ON' if enable else 'OFF'}")
    except Exception as e:
        UI_INFO(f"[MOUSEMODE] toggle error: {e}")

# [선택] 앱 시작 시 항상 켜고, 종료 시 끄고 싶다면 노출용 헬퍼
def enable_global_mouse_mode():
    _set_xterm_mouse_tracking(True)
    atexit.register(lambda: _set_xterm_mouse_tracking(False))

def _ui_logger():
    """
    루트 로거 설정과 무관하게 debug_sc.log로 INFO를 보장 출력하는 전용 로거(ui.scroll)를 만든다.
    - propagate=False: 루트로 전파하지 않음 (루트 레벨/핸들러 영향 제거)
    - FileHandler(debug_sc.log, append)
    """
    global _UI_LOGGER
    if _UI_LOGGER is not None:
        return _UI_LOGGER
    logger = logging.getLogger("ui.scroll")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        fn = os.getenv("PDEX_UI_LOG_FILE", "debug_sc.log")
        fh = logging.FileHandler(fn, mode="a", encoding="utf-8")
        fh.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        # 핸들러 장착 실패해도 이후 _ui_write로 보강
        pass
    _UI_LOGGER = logger
    return logger

def _ui_write(msg: str):
    """
    최후 보루: 로깅이 막힌 환경에서도 파일에 직접 append.
    예외는 조용히 무시.
    """
    try:
        fn = os.getenv("PDEX_UI_LOG_FILE", "debug_sc.log")
        with open(fn, "a", encoding="utf-8") as f:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"{ts} - {msg}\n")
    except Exception:
        pass

def UI_INFO(msg: str):
    try:
        _ui_logger().info(msg)
    except Exception:
        pass
    _ui_write(msg)

class ScrollBar(urwid.WidgetWrap):
    """
    드래그 가능한 세로 스크롤바. ScrollableListBox에 attach() 해서 사용합니다.
    - 마우스 휠/클릭/드래그 처리
    - 뷰포트 크기 변경/리스트 길이 변경 시 썸 위치 보정
    """
    def __init__(self, width: int = 1):
        self.width = max(1, int(width))
        self._pile = urwid.Pile([])
        self._box  = urwid.Filler(self._pile, valign='top')
        super().__init__(self._box)

        self._total: int = 0
        self._first: int = 0
        self._height: int = 1
        self._thumb_top: int = 0
        self._thumb_size: int = 1

        self._dragging: bool = False
        self._drag_anchor: int = 0
        self._target: "ScrollableListBox | None " = None
        self._drag_start_top: int = 0
        self._widget_top = 0

        self._drag_start_row = None
        self._drag_start_thumb_top = None

    def attach(self, listbox: "ScrollableListBox") -> None:
        self._target = listbox
        UI_INFO(f"[SB] attach -> target id={id(listbox)}")

    def selectable(self) -> bool:
        return True

    def update(self, total: int, first: int, height: int) -> None:
        old_total = self._total
        self._total = max(0, int(total))
        self._height = max(1, int(height))
        h = self._height

        #UI_INFO(f"[SB.update] total={self._total} first={first} h={h} dragging={self._dragging}")
        
        if self._total <= 0 or self._total <= h:
            lines = [urwid.Text(" " * self.width) for _ in range(h)]
            self._pile.contents = [(t, ('pack', None)) for t in lines]
            self._invalidate()
            return

        old_thumb_size = self._thumb_size
        self._thumb_size = max(1, int(h * h / self._total))
        track_space = h - self._thumb_size

        if self._dragging:
            if old_total != self._total and old_total > 0:
                old_track = h - old_thumb_size
                if old_track > 0:
                    ratio = self._thumb_top / old_track
                    self._thumb_top = int(ratio * track_space)
            self._thumb_top = max(0, min(self._thumb_top, track_space))
        else:
            self._first = max(0, int(first))
            max_first = max(0, self._total - h)
            ratio = (self._first / max_first) if max_first > 0 else 0.0
            self._thumb_top = int(ratio * track_space)
            if self._first >= max_first:
                self._thumb_top = track_space

        lines = []
        for r in range(h):
            if self._thumb_top <= r < self._thumb_top + self._thumb_size:
                lines.append(urwid.AttrMap(urwid.Text(THUMB_CHAR * self.width), 'scroll_thumb'))
            else:
                lines.append(urwid.AttrMap(urwid.Text(TRACK_CHAR * self.width), 'scroll_bar'))
        self._pile.contents = [(t, ('pack', None)) for t in lines]
        self._invalidate()

    def _real_total(self) -> int:
        try:
            if self._target and hasattr(self._target, "body") and hasattr(self._target.body, "__len__"):
                return int(len(self._target.body))
        except Exception:
            pass
        return int(self._total)

    def mouse_event(self, size, event, button, col, row, focus):
        if self._target is None:
            return False

        h = (size[1] if isinstance(size, tuple) and len(size) > 1 else self._height)
        # 표시 여부 판단은 self._total 기준(visual_total 보정 포함)
        if self._total <= 0 or self._total <= h:
            return False

        real_total = max(0, self._real_total())
        UI_INFO(f"[SB.mouse] event={event} btn={button} col={col} row={row} h={h} real_total={real_total} total={self._total} thumb_top={self._thumb_top} thumb_size={self._thumb_size}")

        def _apply_focus_move(new_focus: int, coming='above'):
            if real_total <= 0:
                UI_INFO("[SB.mouse] skip focus move: real_total<=0")
                return
            new_focus = max(0, min(int(new_focus), real_total - 1))
            UI_INFO(f"[SB.move] -> focus={new_focus}")
            try:
                self._target.set_focus(new_focus, coming_from=coming)
            except Exception as e:
                UI_INFO(f"[SB.move] set_focus error: {e}")
            self._target._invalidate()
            self._invalidate()

        def _pos_from_track(top_pos: int) -> int:
            track_space = max(1, h - self._thumb_size)
            tp = max(0, min(int(top_pos), track_space))
            ratio = (tp / track_space) if track_space > 0 else 0.0
            pos = int(round(ratio * max(0, real_total - 1)))
            UI_INFO(f"[SB.pos] top_pos={top_pos} -> pos={pos} (track_space={track_space})")
            return pos

        local_row = int(row)
        is_thumb = (self._thumb_top <= local_row < self._thumb_top + self._thumb_size)

        if event == 'mouse press' and button == 1:
            UI_INFO(f"[SB.press] is_thumb={is_thumb}")
            #_set_xterm_mouse_tracking(True)

            self._global_drag_start_row = None
            self._global_drag_start_col = None
            self._last_global_row = None
            self._last_global_col = None

            if is_thumb:
                self._dragging = True
                self._drag_anchor = local_row - self._thumb_top
                self._drag_start_row = int(row)
                self._drag_start_thumb_top = self._thumb_top
                if hasattr(self._target, '_register_global_drag'):
                    self._target._register_global_drag(self)
                UI_INFO(f"[SB.drag.start] anchor={self._drag_anchor}")
                return True
            else:
                # [수정] 트랙 클릭도 드래그로 전환되도록 처리
                # 1) 클릭 지점 기준으로 썸 위치를 즉시 옮김
                desired_top = local_row - (self._thumb_size // 2)
                track_space = max(1, h - self._thumb_size)
                self._thumb_top = max(0, min(int(desired_top), track_space))
                UI_INFO(f"[SB.track.click] desired_top={desired_top} -> thumb_top={self._thumb_top}")

                # 2) 해당 위치에 매핑되는 포커스로 즉시 점프
                target_focus = _pos_from_track(self._thumb_top)
                _apply_focus_move(target_focus, coming='above')

                # 3) 곧바로 드래그 모드 진입 (트랙에서 눌러도 드래그 가능)
                self._dragging = True                           # [수정]
                self._drag_anchor = local_row - self._thumb_top # [수정]
                self._drag_start_row = int(row)                 # [수정]
                self._drag_start_thumb_top = self._thumb_top    # [수정]
                if hasattr(self._target, '_register_global_drag'):
                    self._target._register_global_drag(self)     # [수정]
                UI_INFO(f"[SB.drag.start@track] anchor={self._drag_anchor}")  # [수정]
                return True


        if event == 'mouse drag' and button == 1 and self._dragging:
            desired_top = local_row - self._drag_anchor
            track_space = max(1, h - self._thumb_size)
            self._thumb_top = max(0, min(int(desired_top), track_space))
            UI_INFO(f"[SB.drag.local] desired_top={desired_top} -> thumb_top={self._thumb_top}")
            target_focus = _pos_from_track(self._thumb_top)
            _apply_focus_move(target_focus, coming='above')
            return True

        if event == 'mouse release':
            UI_INFO(f"[SB.release] dragging={self._dragging}")
            if self._dragging:
                self._dragging = False
                if hasattr(self._target, '_unregister_global_drag'):
                    self._target._unregister_global_drag()
                return True

        return False

    def handle_local_drag(self, local_row):
        if not self._dragging or not self._target:
            return
        desired_top = local_row - self._drag_anchor
        self._handle_drag_to_position(desired_top)

    def handle_global_drag(self, global_row_col):
        if not self._dragging or not self._target:
            return
        
        # (col,row) 언팩 (하위호환: 숫자 하나면 row만)
        if isinstance(global_row_col, tuple) and len(global_row_col) >= 2:
            try:
                gcol = int(global_row_col[0]); grow = int(global_row_col[1])
            except Exception:
                gcol = getattr(self, "_last_global_col", 0)
                grow = getattr(self, "_last_global_row", 0)
        else:
            gcol = getattr(self, "_last_global_col", 0)
            try:
                grow = int(global_row_col)
            except Exception:
                grow = getattr(self, "_last_global_row", 0)
        
        # 최초 콜에서 기준점 기록
        if getattr(self, "_global_drag_start_row", None) is None:
            self._global_drag_start_row = grow
            self._global_drag_start_col = gcol
            if getattr(self, "_drag_start_thumb_top", None) is None:
                self._drag_start_thumb_top = self._thumb_top
            UI_INFO(f"[SB.drag.global.init] start_row={self._global_drag_start_row}, start_col={self._global_drag_start_col}, start_thumb={self._drag_start_thumb_top}")

        # 이전 좌표 보관
        self._last_global_row = grow
        self._last_global_col = gcol

        # 기본 delta
        delta_row = grow - self._global_drag_start_row
        delta_col = gcol - self._global_drag_start_col

        # [핵심 보정] row 변동이 0인데 col만 변하면 col 방향을 세로 1칸으로 해석
        if delta_row == 0 and delta_col != 0:
            synth = 1 if delta_col > 0 else -1
            delta_row = synth
            UI_INFO(f"[SB.drag.global.fallback] grow={grow}, gcol={gcol}, delta_col={delta_col} -> synth delta_row={synth}")

        new_thumb_top = self._drag_start_thumb_top + delta_row
        UI_INFO(f"[SB.drag.global] grow={grow} gcol={gcol} delta_row={delta_row} new_thumb_top={new_thumb_top}")

        self._handle_drag_to_position(new_thumb_top)

    def _handle_drag_to_position(self, desired_top):
        UI_INFO(f"[SB.drag.apply] desired_top={desired_top}")
        self._total = len(self._target.body) if hasattr(self._target.body, '__len__') else 0
        h = self._height
        self._thumb_size = max(1, int(h * h / self._total))
        track_space = max(1, h - self._thumb_size)

        desired_top = max(0, min(desired_top, track_space))
        self._thumb_top = desired_top

        if desired_top <= 0:
            new_first = 0
        elif desired_top >= track_space:
            new_first = max(0, self._total - h)
        else:
            ratio = desired_top / track_space
            max_first = max(0, self._total - h)
            new_first = int(round(ratio * max_first))

        self._first = new_first
        max_first = max(0, self._total - h)
        new_first = max(0, min(new_first, max_first))

        if new_first == 0:
            try:
                self._target.set_focus(0, coming_from='above')
                self._target._stored_first = 0
            except:
                pass
        elif new_first >= max_first:
            try:
                self._target.set_focus(self._total - 1, coming_from='below')
                self._target._stored_first = max_first
            except:
                pass
        else:
            try:
                self._target.set_focus(new_first, coming_from='above')
                self._target._stored_first = new_first
            except:
                pass

        try:
            self._invalidate()
            self._target._invalidate()
        except Exception:
            pass

class ScrollableListBox(urwid.ListBox):
    """
    - 마우스 휠: 화면 경계(top/bottom) 기준으로 1줄씩 스크롤
    - PageUp/Down: 화면 높이 h에서 page_overlap 줄을 겹치고 이동(h - overlap)
    - get_view_indices(): 현재 화면의 (Top, Cursor, Bottom) 정확한 인덱스 반환
    """
    def __init__(self, body, scrollbar: ScrollBar | None = None,
                 enable_selection: bool = True, auto_scroll: bool = False,
                 page_overlap: int = 1):
        super().__init__(body)
        self._last_size: Tuple[int, int] = (1, 1)
        self._last_h: int = 1
        self._scrollbar: ScrollBar | None = scrollbar
        self._sel: int | None = None
        self._enable_selection = enable_selection
        self._stored_first: int | None = None
        self._auto_scroll = auto_scroll
        self._has_focus = False
        self._app_ref = None
        self._page_overlap = max(0, int(page_overlap))

        if self._enable_selection and hasattr(self.body, '__len__') and len(self.body) > 0:
            self._sel = 0
            self._apply_selection(0)

    # [추가] 행 위젯의 '가시 줄 수'를 현재 너비 기준으로 추정
    def _row_visual_height(self, w, maxcol: int) -> int:
        """
        주어진 행 위젯(w)이 현재 너비(maxcol)에서 차지할 '가시 줄 수'를 추정.
        실패 시 최소 1을 반환.
        """
        try:
            base = getattr(w, "base_widget", w)
            # flow 위젯은 rows((maxcol,), focus) 사용
            return max(1, int(base.rows((maxcol,), False)))
        except Exception:
            # rows 호출이 어렵다면 1로 폴백
            return 1

    # [추가] 현재 body 전체의 '가시 줄 수' 합계를 추정
    def _estimate_total_visual_lines(self, maxcol: int) -> int:
        try:
            body = getattr(self, "body", None)
            if not body:
                return 0
            total = 0
            for w in body:
                total += self._row_visual_height(w, maxcol)
            return total
        except Exception:
            return 0

    # App 참조(전역 드래그 state 저장 용도)
    def set_app_ref(self, app):
        self._app_ref = app

    # 전역 드래그 시작/종료 시 App에 상태 공유
    def _register_global_drag(self, scrollbar):
        if self._app_ref is not None:
            setattr(self._app_ref, "_dragging_scrollbar", scrollbar)

    def _unregister_global_drag(self):
        if self._app_ref is not None:
            setattr(self._app_ref, "_dragging_scrollbar", None)

    def _apply_selection(self, new_sel: int | None):
        if not self._enable_selection:
            return
        if self._sel != new_sel:
            if self._sel is not None and 0 <= self._sel < len(self.body):
                try:
                    self.body[self._sel].set_attr_map({None: None})
                except:
                    pass
            self._sel = new_sel
            if self._sel is not None and 0 <= self._sel < len(self.body):
                try:
                    self.body[self._sel].set_attr_map({None: 'line_focus'})
                except:
                    pass

    def _get_actual_first(self):
        try:
            focus = self.focus_position
            total = len(self.body) if hasattr(self.body, '__len__') else 0
            if self._stored_first is not None:
                first = self._stored_first
                if first >= total - self._last_h and focus == total - 1:
                    first = max(0, total - self._last_h)
                self._stored_first = None
                return first
            if focus < self._last_h // 2:
                return 0
            if focus > total - self._last_h // 2:
                return max(0, total - self._last_h)
            return max(0, focus - self._last_h // 2)
        except:
            return 0

    def render(self, size, focus=False):
        self._has_focus = focus
        self._last_size = size
        maxcol, maxrow = (size + (1,))[:2]
        self._last_col = maxcol
        h = self._last_h = max(1, maxrow)
        body_len = len(self.body) if hasattr(self.body, '__len__') else 0

        # [추가] 가시 줄 수(visual lines) 추정 → 스크롤 표시 여부 보정
        visual_total = self._estimate_total_visual_lines(maxcol)

        # ListBox의 실제 first(아이템 인덱스) 계산은 기존 로직 사용
        actual_first = self._get_actual_first()

        # [핵심 보정]
        # - 원래 ScrollBar는 total<=h면 감춤 → 카드 리스트는 아이템 수가 적어 감춰짐
        # - 가시 줄 수가 높이보다 크면 '최소한으로라도' 보이도록 total을 h+1로 승격
        total_for_scrollbar = body_len
        if visual_total > h and body_len <= h:
            total_for_scrollbar = h + 1  # 최소 한 칸이라도 트랙/썸이 보이게

        if self._scrollbar:
            self._scrollbar.update(total=total_for_scrollbar, first=actual_first, height=h)
        return super().render(size, focus)
    
    def get_view_indices(self):
        """
        현재 뷰포트의 (Top, Cursor, Bottom) 인덱스 반환.
        urwid.ListBox.calculate_visible 포맷:
          middle: (row_offset, widget, position, rows, cursor)
          top:    (trim_lines, [(widget, pos, rows), ...])
          bottom: (trim_lines, [(widget, pos, rows), ...])
        """
        if not self.body or not self._last_size:
            return None, None, None
        try:
            middle, top, bottom = self.calculate_visible(self._last_size, self._has_focus)
        except Exception as e:
            logging.debug(f"calculate_visible failed: {e}")
            try:
                return None, int(self.focus_position), None
            except:
                return None, None, None

        visible_indexes = []
        cur_focus = None
        if middle and isinstance(middle, (tuple, list)) and len(middle) > 2 and isinstance(middle[2], int):
            cur_focus = middle[2]
            visible_indexes.append(middle[2])

        def _collect(stack):
            if not stack or not isinstance(stack, (tuple, list)) or len(stack) < 2:
                return
            lst = stack[1]
            if isinstance(lst, (list, tuple)):
                for item in lst:
                    if isinstance(item, (tuple, list)) and len(item) > 1 and isinstance(item[1], int):
                        visible_indexes.append(item[1])

        _collect(top)
        _collect(bottom)

        clean = [x for x in visible_indexes if isinstance(x, int) and x >= 0]
        if not clean:
            return None, cur_focus, None
        return min(clean), cur_focus, max(clean)

    def scroll_to_bottom(self):
        total = len(self.body) if hasattr(self.body, '__len__') else 0
        if total > 0:
            try:
                self.set_focus(total - 1, coming_from='below')
            except:
                pass

    def is_at_bottom(self):
        try:
            total = len(self.body) if hasattr(self.body, '__len__') else 0
            if total == 0:
                return True
            focus = self.focus_position
            return focus >= total - 1
        except:
            return True

    def _navigate_to(self, new_focus, update_selection=True):
        total = len(self.body) if hasattr(self.body, '__len__') else 0
        if total <= 0:
            return
        new_focus = max(0, min(new_focus, total - 1))
        try:
            self.set_focus(new_focus)
        except:
            pass
        if update_selection:
            self._apply_selection(new_focus)
        self._invalidate()

    def _scroll_view(self, delta: int):
        top_idx, cur_idx, bot_idx = self.get_view_indices()
        total = len(self.body) if hasattr(self.body, '__len__') else 0

        # [추가] 상세 로깅
        UI_INFO(f"[_scroll_view] delta={delta}, top={top_idx}, cur={cur_idx}, bot={bot_idx}, total={total}")

        if total <= 0:
            logging.warning(f"[_scroll_view] Empty body, skipping")
            return

        base = top_idx if delta < 0 else bot_idx
        if base is None:
            try:
                base = int(self.focus_position)
            except:
                base = 0
        new_focus = max(0, min(int(base) + delta, total - 1))
        
        UI_INFO(f"[_scroll_view] base={base} -> new_focus={new_focus}")

        if new_focus != base:
            try:
                coming = 'above' if delta > 0 else 'below'
                self.set_focus(new_focus, coming_from=coming)
                logging.debug(f"[_scroll_view] Focus changed successfully")
            except Exception as e:
                logging.error(f"[_scroll_view] set_focus failed: {e}")
        else:
            logging.debug(f"[_scroll_view] Focus unchanged (boundary)")
        self._invalidate()

    def mouse_event(self, size, event, button, col, row, focus):
        UI_INFO(f"[ScrollableListBox] mouse_event: {event} button={button}")
        # 마우스 휠: 1줄 스크롤
        if event == 'mouse press' and button in (4, 5):
            delta = -1 if button == 4 else 1
            logging.debug(f"[ScrollableListBox.wheel] button={button} delta={delta}")
            self._scroll_view(delta)
            return True
        if event == 'mouse press' and button == 1:
            logging.debug(f"[ScrollableListBox.click] col={col} row={row}")
            ret = super().mouse_event(size, event, button, col, row, focus)
            if self._enable_selection:
                try:
                    self._apply_selection(int(self.focus_position))
                except:
                    pass
            return ret
        return super().mouse_event(size, event, button, col, row, focus)

    def keypress(self, size, key):
        h = (size[1] if len(size) > 1 else self._last_h)
        total = len(self.body) if hasattr(self.body, '__len__') else 0
        try:
            cur = int(self.focus_position)
        except:
            cur = 0

        if key == 'up' and cur == 0:
            return None
        if key == 'down' and cur == total - 1:
            return None

        # PageUp/Down: h - overlap 만큼 (겹침 유지)
        step = max(1, h - self._page_overlap)

        if key == 'page up':
            self._navigate_to(max(0, cur - step))
            return None
        if key == 'page down':
            self._navigate_to(min(total - 1, cur + step))
            return None
        if key == 'home':
            self._navigate_to(0)
            return None
        if key == 'end':
            self._navigate_to(total - 1)
            return None

        ret = super().keypress(size, key)
        if self._enable_selection:
            try:
                self._apply_selection(int(self.focus_position))
            except:
                pass
        return ret

def hook_global_mouse_events(loop: urwid.MainLoop, app) -> None:
    """
    스크롤바 드래그를 전역으로 부드럽게 처리하기 위해 MainLoop.process_input을 후킹합니다.
    App은 다음 속성을 가질 것을 권장합니다:
      - app._dragging_scrollbar: Optional[ScrollBar]
      - (선택) app._pending_logs: list[str]
      - (선택) app._on_scrollbar_drag_end(scrollbar): 후킹 종료 시 호출
    """
    original_process = loop.process_input

    def process_with_global_drag(keys):
        dragging = getattr(app, "_dragging_scrollbar", None)

        # 드래그 미활성: press 좌표만 미리 저장, 원래 처리 먼저
        if not dragging:
            for key in keys:
                if isinstance(key, tuple) and len(key) >= 4 and key[0] == 'mouse press':
                    try:
                        setattr(app, "_last_press_pos", (int(key[2]), int(key[3])))
                        UI_INFO(f"[HOOK] press seen before process: start_pos={app._last_press_pos}")
                    except Exception as e:
                        UI_INFO(f"[HOOK] press pre-capture error: {e}")
            res = original_process(keys)

            # press 전달 결과로 드래그가 시작되었으면 시작 좌표 주입
            dragging = getattr(app, "_dragging_scrollbar", None)
            if dragging:
                start_pos = getattr(app, "_last_press_pos", None)
                if start_pos and getattr(dragging, "_global_drag_start_row", None) is None:
                    try:
                        dragging._global_drag_start_col = int(start_pos[0])
                        dragging._global_drag_start_row = int(start_pos[1])
                        if getattr(dragging, "_drag_start_thumb_top", None) is None:
                            dragging._drag_start_thumb_top = dragging._thumb_top
                        UI_INFO(f"[HOOK] press captured -> start_pos={start_pos}, start_thumb={dragging._drag_start_thumb_top}")
                    except Exception as e:
                        UI_INFO(f"[HOOK] press init error: {e}")
            return res

        # 드래그 활성: drag/release를 선처리, 나머지 키만 원래 루프로
        new_keys = []
        for key in keys:
            if isinstance(key, tuple) and len(key) >= 4:
                UI_INFO(f"[HOOK] raw mouse key={key}")
                et = key[0]
                try:
                    col = int(key[2]); row = int(key[3])
                except Exception:
                    col = 0; row = 0

                if et == 'mouse drag':
                    try:
                        dragging.handle_global_drag((col, row))  # (col,row) 전달
                        loop.draw_screen()
                    except Exception as e:
                        UI_INFO(f"[HOOK] handle_global_drag error: {e}")
                    continue

                if et == 'mouse release':
                    UI_INFO("[HOOK] mouse release, finishing drag")
                    try:
                        dragging._dragging = False
                    except Exception:
                        pass
                    setattr(app, "_dragging_scrollbar", None)
                    cb = getattr(app, "_on_scrollbar_drag_end", None)
                    if callable(cb):
                        try:
                            cb(dragging)
                        except Exception as e:
                            UI_INFO(f"[HOOK] drag_end callback error: {e}")
                    #_set_xterm_mouse_tracking(False)
                    continue

            new_keys.append(key)

        return original_process(new_keys)

    loop.process_input = process_with_global_drag