#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import urwid
import sys, locale, logging
from typing import Tuple
import math
import logging
logger = logging.getLogger(__name__)

def _detect_encoding() -> str:
    return (sys.stdout.encoding or locale.getpreferredencoding(False) or "ascii").lower()

_USE_UTF8 = ("utf" in _detect_encoding())
TRACK_CHAR = "│" if _USE_UTF8 else "|"
THUMB_CHAR = "█" if _USE_UTF8 else "@"

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

        
        self._first: int = 0
        self._height: int = 1
        self._thumb_top: int = 0
        self._thumb_size: int = 1

        self._dragging: bool = False
        self._drag_anchor: int = 0
        self._target: "ScrollableListBox | None " = None

        self._drag_start_thumb_top = None

        self._item_total = 0       # 실제 아이템 개수 (드래그 -> 인덱스 매핑에 사용)
        self._visual_total = 0     # 시각적 총 줄 수 (썸 크기 계산에만 사용)
        self._avg_lines = 1.0        # [ADD] 아이템당 평균 줄 수 (visual_total/item_total)
        self._visual_mode = False    # [ADD] 시각 기준 모드 여부

        self._max_first_cards: int = 0   # [ADD] 가상 모드에서의 first 상한을 보관
        self._card_count: int = 0        # [ADD] 가상 모드일 때 카드 개수 캐시

    def _draw(self, draw_h: int, src: str = "update"):
        draw_h = max(1, int(draw_h))
        # 숨김이면 공백으로
        if (self._visual_total <= self._height) or (self._item_total == 0):
            lines = [urwid.Text(" " * self.width) for _ in range(draw_h)]
            self._pile.contents = [(t, ('pack', None)) for t in lines]
            return

        # (화면 높이 기준으로 바로 그리기: 논리→그리기 스케일 필요 없음. 이미 update가 self._height=h 로 계산)
        draw_top  = self._thumb_top
        draw_size = self._thumb_size

        # 실제 그리기
        lines = []
        for r in range(draw_h):
            if draw_top <= r < draw_top + draw_size:
                lines.append(urwid.AttrMap(urwid.Text(THUMB_CHAR * self.width), 'scroll_thumb'))
                #lines.append(urwid.Text(THUMB_CHAR * self.width))
            else:
                lines.append(urwid.AttrMap(urwid.Text(TRACK_CHAR * self.width), 'scroll_bar'))
                #lines.append(urwid.Text(TRACK_CHAR * self.width))
        self._pile.contents = [(t, ('pack', None)) for t in lines]
        self._invalidate()

    def _handle_drag_to_position(self, desired_top):
        h = self._height
        track_space = max(1, h - self._thumb_size)
        desired_top = max(0, min(int(desired_top), track_space))
        self._thumb_top = desired_top
        ratio = desired_top / track_space if track_space > 0 else 0.0

        # 드래그 비율 → (가상) 인덱스
        if self._visual_mode:
            avg = max(1e-9, self._avg_lines)
            per_view = h / avg
            max_first = self._max_first_cards if hasattr(self, "_max_first_cards") else max(0, int(math.ceil(self._item_total - per_view)))
            virt_idx = max(0, min(max_first, int(round(ratio * max_first))))
        else:
            max_idx = max(0, self._item_total - 1)
            virt_idx = max(0, min(max_idx, int(round(ratio * max_idx))))

        # [핵심] 최상/최하 자석 보정: 끝으로 내려오면 '마지막 아이템'으로 포커스 강제
        go_top    = (ratio <= 0.001 or desired_top <= 0)
        go_bottom = (ratio >= 0.999 or desired_top >= track_space)

        # 기본 목표(가상 → 실제 body)
        target_body_idx = virt_idx
        if self._target and getattr(self._target, "_virtual_index_mode", False):
            if hasattr(self._target, "map_virtual_to_body_index"):
                try:
                    mapped = self._target.map_virtual_to_body_index(virt_idx)
                    if isinstance(mapped, int) and mapped >= 0:
                        target_body_idx = mapped
                except Exception:
                    mapped = None

        # [보정] 맨 위/맨 아래일 땐 첫/마지막 '아이템'으로 강제
        if go_top:
            target_body_idx = 0
        elif go_bottom:
            try:
                # 가상 모드면 '마지막 카드의 body 인덱스'로, 일반 모드면 마지막 아이템으로
                if self._visual_mode and hasattr(self._target, "map_virtual_to_body_index"):
                    last_card = max(0, (self._card_count or 1) - 1)
                    last_body_idx = self._target.map_virtual_to_body_index(last_card)
                    if isinstance(last_body_idx, int) and last_body_idx >= 0:
                        target_body_idx = last_body_idx
                    else:
                        # 폴백: body 마지막
                        if hasattr(self._target, "body") and hasattr(self._target.body, "__len__"):
                            target_body_idx = len(self._target.body) - 1
                else:
                    if hasattr(self._target, "body") and hasattr(self._target.body, "__len__"):
                        target_body_idx = len(self._target.body) - 1
            except Exception:
                pass

        # set_focus 적용
        if self._target and hasattr(self._target, "set_focus"):
            try:
                cur = int(self._target.focus_position)
            except Exception:
                cur = target_body_idx
            coming = 'below' if target_body_idx > cur else 'above'
            try:
                self._target.set_focus(target_body_idx, coming_from=coming)
                if hasattr(self._target, "_apply_sticky_inner_focus"):
                    self._target._apply_sticky_inner_focus()
            except Exception:
                pass

        self._invalidate()
        if self._target:
            self._target._invalidate()

    def attach(self, listbox: "ScrollableListBox") -> None:
        self._target = listbox

    def selectable(self) -> bool:
        return False

    def update(self, total: int, first: int, height: int, visual_total: int = None) -> None:
        self._item_total = max(0, int(total))
        self._height = max(1, int(height))
        vtotal = int(visual_total) if visual_total is not None else self._item_total
        self._visual_total = max(self._item_total, vtotal)
        self._visual_mode = (visual_total is not None and self._visual_total > self._item_total)
        self._avg_lines = (self._visual_total / self._item_total) if self._item_total > 0 else 1.0
        self._card_count = self._item_total  # [ADD] (가상모드일 때 카드 수)

        h = self._height

        # 스크롤바 숨김 판단
        if (self._visual_total <= h) or (self._item_total == 0):
            lines = [urwid.Text(" " * self.width) for _ in range(h)]
            self._pile.contents = [(t, ('pack', None)) for t in lines]
            self._thumb_size = h
            self._thumb_top = 0
            self._invalidate()
            return

        # 썸 크기(논리) – 반올림, 최소/최대 보정으로 track >= 1 보장
        thumb_calc = int(round((h * h) / float(self._visual_total)))
        self._thumb_size = max(1, min(h - 1, thumb_calc))
        track_space = max(1, h - self._thumb_size)

        # 썸 위치(논리)
        if not self._dragging:
            self._first = max(0, int(first))
            if self._visual_mode:
                # 카드 상한 기반 비율
                avg_per_item = max(1e-9, self._avg_lines)
                cards_per_view = h / avg_per_item
                max_first_cards = max(0, int(math.ceil(self._item_total - cards_per_view)))
                self._max_first_cards = max_first_cards  # [ADD] 보관
                self._first = min(self._first, max_first_cards)
                pos_ratio = 0.0 if max_first_cards == 0 else (self._first / max_first_cards)
            else:
                self._max_first_cards = 0               # [ADD] 일반 모드에서는 의미 없음
                max_index = max(1, self._item_total - 1)
                pos_ratio = min(1.0, self._first / max_index)

            self._thumb_top = min(track_space, int(round(pos_ratio * track_space)))
        else:
            self._thumb_top = max(0, min(self._thumb_top, track_space))

        # [그리기] 논리 높이(h) 기준으로 바로 그립니다.
        self._draw(h, src="update")

        
    # [교체] render: 더 이상 강제 그리기/스케일 변환을 하지 않고 그대로 위임합니다.
    def render(self, size, focus=False):
        return super().render(size, focus)

    # [교체] mouse_event: 좌표는 논리 h 기준(간단/안정). 휠은 ListBox로 위임.
    def mouse_event(self, size, event, button, col, row, focus):
        if self._target is None:
            return False

        h = (size[1] if isinstance(size, tuple) and len(size) > 1 else self._height)
        if (self._visual_total <= self._height) or (self._item_total == 0):
            return False

        local_row = int(row)
        if event == 'mouse press' and button in (4, 5):
            delta = -1 if button == 4 else 1
            try:
                if getattr(self._target, "_virtual_index_mode", False) and hasattr(self._target, "_scroll_by_cards"):
                    # 카드 리스트: 항상 카드 1장 단위로
                    self._target._scroll_by_cards(delta_cards=delta)
                elif hasattr(self._target, "_scroll_view"):
                    # 일반 리스트: 아이템 단위
                    self._target._scroll_view(delta)
            except Exception:
                pass
            return True

        if event == 'mouse press' and button == 1:
            desired_top = local_row - (self._thumb_size // 2)
            self._handle_drag_to_position(desired_top)
            self._dragging = True
            self._drag_anchor = local_row - self._thumb_top
            self._drag_start_thumb_top = self._thumb_top
            if hasattr(self._target, '_register_global_drag'):
                self._target._register_global_drag(self)
            return True

        if event == 'mouse drag' and button == 1 and self._dragging:
            desired_top = local_row - self._drag_anchor
            self._handle_drag_to_position(desired_top)
            return True

        if event == 'mouse release':
            if self._dragging:
                self._dragging = False
                if hasattr(self._target, '_unregister_global_drag'):
                    self._target._unregister_global_drag()
                return True

        return False
    
    def handle_global_drag(self, global_row_col):
        if not self._dragging or not self._target:
            return
        
        # (col,row) 언팩 (하위호환: 숫자 하나면 row만)
        if isinstance(global_row_col, tuple) and len(global_row_col) >= 2:
            try:
                grow = int(global_row_col[1])
            except Exception:
                grow = getattr(self, "_last_global_row", 0)
        else:
            try:
                grow = int(global_row_col)
            except Exception:
                grow = getattr(self, "_last_global_row", 0)
        
        # 최초 콜에서 기준점 기록
        if getattr(self, "_global_drag_start_row", None) is None:
            self._global_drag_start_row = grow
            if getattr(self, "_drag_start_thumb_top", None) is None:
                self._drag_start_thumb_top = self._thumb_top
            
        # 이전 좌표 보관
        self._last_global_row = grow

        # 기본 delta
        delta_row = grow - self._global_drag_start_row

        new_thumb_top = self._drag_start_thumb_top + delta_row

        self._handle_drag_to_position(new_thumb_top)


class ScrollableListBox(urwid.ListBox):
    def __init__(self, body, scrollbar=None,
                 enable_selection=True,
                 page_overlap=1,
                 use_visual_total=False,
                 fixed_lines_per_item: int = 0,
                 count_only_pile_as_item: bool = False):
        super().__init__(body)
        self._scrollbar = scrollbar
        self._use_visual_total = bool(use_visual_total)
        self._fixed_lines_per_item = int(fixed_lines_per_item)
        self._count_only_pile_as_item = bool(count_only_pile_as_item)
        self._virtual_index_mode = False

        self._last_size: Tuple[int, int] = (1, 1)
        self._last_h: int = 1
        self._sel: int | None = None
        self._enable_selection = enable_selection
        self._stored_first: int | None = None
        self._has_focus = False
        self._app_ref = None
        self._page_overlap = max(0, int(page_overlap))

        # [ADD] 선택 하이라이트 잠금(기본 ON 권장)
        self._lock_selection: bool = True
        self._sticky_col_idx: int | None = None   # [ADD] 마지막으로 사용자가 선택한 칼럼 인덱스(Q 등)

        if self._enable_selection and hasattr(self.body, '__len__') and len(self.body) > 0:
            self._sel = 0
            self._apply_selection(0)

    # [ADD] 현재 카드의 Controls Columns 반환
    def _current_card_controls(self):
        try:
            focus_widget, pos = self.get_focus()
            base = getattr(focus_widget, "base_widget", focus_widget)
            if isinstance(base, urwid.Pile):
                controls = base.contents[0][0]
                if isinstance(controls, urwid.Columns):
                    return controls
        except: pass
        return None

    # [ADD] 사용자가 클릭/키로 바꾼 칼럼을 sticky 로 기억
    def _update_sticky_from_current(self):
        cols = self._current_card_controls()
        if isinstance(cols, urwid.Columns):
            idx = None
            # Q 우선
            q = self._find_q_col_index(cols)
            if q is not None:
                idx = q
            else:
                # Q가 아닌 칼럼을 클릭한 경우에는 그 칼럼을 기억
                try:
                    idx = cols.focus_position
                except Exception:
                    _, idx = cols.get_focus()
            if isinstance(idx, int) and idx >= 0:
                self._sticky_col_idx = idx

    # [NEW] 현재 카드 Controls에서 'Q:' 캡션을 가진 Edit가 몇 번째 칼럼인지 탐색
    def _find_q_col_index(self, cols: urwid.Columns) -> int | None:
        try:
            for j, (w, _) in enumerate(cols.contents):
                base = getattr(w, "base_widget", w)
                # AttrMap(Edit(...)) 구조: base_widget이 Edit
                if isinstance(base, urwid.Edit):
                    cap = str(base.get_caption() or "")
                    if cap.strip().startswith("Q:"):
                        return j
        except Exception:
            pass
        return None

    # [ADD] sticky 칼럼을 새 카드에 적용(렌더 1틱 후 보장)
    def _apply_sticky_inner_focus(self, delay: float = 0.0):
        if self._app_ref is None or not getattr(self._app_ref, "loop", None):
            return

        def _apply(loop, data):
            try:
                focus_widget, _ = self.get_focus()
                base = getattr(focus_widget, "base_widget", focus_widget)

                if not isinstance(base, urwid.Pile):
                    return
                # 1) 카드 Pile의 포커스를 0행(controls)으로
                try:
                    base.focus_position = 0
                except Exception:
                    pass

                # 2) Controls Columns에서 Q 칼럼 인덱스 찾기
                cols = base.contents[0][0]
                if not isinstance(cols, urwid.Columns):
                    return

                # (1) 우선 Q: 캡션 탐색
                q_idx = self._find_q_col_index(cols)

                # (2) Q를 못 찾으면 기존 sticky_col_idx(있다면) 사용
                if q_idx is None and isinstance(self._sticky_col_idx, int):
                    q_idx = self._sticky_col_idx

                # (3) 둘 다 없으면 현재 포커스 유지
                if q_idx is None:
                    return

                n = len(cols.contents)
                q_idx = max(0, min(q_idx, n - 1))
                try:
                    cols.focus_position = q_idx
                except Exception:
                    pass

                # 화면 갱신
                self._invalidate()
                try:
                    self._app_ref._request_redraw()
                except Exception:
                    pass
            except Exception:
                pass

        # [핵심] 반드시 다음 틱에 적용해야 urwid의 내부 포커스 정렬이 끝난 뒤 반영됩니다.
        self._app_ref.loop.set_alarm_in(0, _apply)

    # [ADD] 선택 잠금 토글 API
    def set_selection_lock(self, on: bool = True):
        self._lock_selection = bool(on)

    def _scroll_by_cards(self, delta_cards: int):
        card_cnt = self._count_cards()
        if card_cnt <= 0: return
        # 현재 칼럼 포커스를 기억
        self._update_sticky_from_current()

        cur_card = self._current_card_index()
        new_card = max(0, min(cur_card + int(delta_cards), card_cnt - 1))
        target_body_idx = self.map_virtual_to_body_index(new_card)
        try:
            coming = 'above' if target_body_idx < int(self.focus_position) else 'below'
        except Exception:
            coming = 'below' if delta_cards > 0 else 'above'
        try:
            self.set_focus(target_body_idx, coming_from=coming)
        except Exception:
            pass
        
        # [핵심] 새 카드의 Controls Columns 포커스를 sticky 인덱스로 강제
        self._apply_sticky_inner_focus()
        # 선택 하이라이트 잠금 상태면 선택 유지
        if not self._lock_selection:
            self._apply_selection(int(self.focus_position))
        self._invalidate()

    # [추가] 카드(Pile) 개수 세기
    def _count_cards(self) -> int:
        try:
            cnt = 0
            for w in self.body:
                base = getattr(w, "base_widget", w)
                if isinstance(base, urwid.Pile):
                    # 카드 Pile의 첫 콘텐츠가 Columns인 경우만 카드로 간주(간단한 식별자)
                    try:
                        if isinstance(base.contents[0][0], urwid.Columns):
                            cnt += 1
                    except Exception:
                        pass
            return cnt
        except Exception:
            return 0

    # [추가] 현재 first(아이템 인덱스) → 카드 인덱스 근사값
    def _approx_first_card_index(self, first_item_idx: int) -> int:
        try:
            card_idx = -1
            acc = 0
            for i, w in enumerate(self.body):
                base = getattr(w, "base_widget", w)
                if isinstance(base, urwid.Pile):
                    try:
                        if isinstance(base.contents[0][0], urwid.Columns):
                            acc += 1
                            card_idx = acc - 1
                    except Exception:
                        pass
                if i >= first_item_idx:
                    break
            return max(0, card_idx)
        except Exception:
            return 0

    # [추가] 가상(카드) 인덱스 → 실제 body 인덱스 매핑 (ScrollBar가 호출)
    def map_virtual_to_body_index(self, virt_idx: int) -> int:
        try:
            acc = 0
            for i, w in enumerate(self.body):
                base = getattr(w, "base_widget", w)
                if isinstance(base, urwid.Pile):
                    try:
                        if isinstance(base.contents[0][0], urwid.Columns):
                            if acc == virt_idx:
                                return i  # 이 행이 virt_idx번째 카드가 있는 실제 body 인덱스
                            acc += 1
                    except Exception:
                        pass
            # 못 찾으면 마지막 카드를 가리키도록
            return i if 'i' in locals() else 0
        except Exception:
            return 0
    
    # [추가] 현재 포커스가 속한 '카드 인덱스(virt)' 구하기
    def _current_card_index(self) -> int:
        try:
            cur = int(self.focus_position)
        except Exception:
            cur = 0
        acc = 0
        last_card_idx = 0
        try:
            for i, w in enumerate(self.body):
                base = getattr(w, "base_widget", w)
                if isinstance(base, urwid.Pile):
                    try:
                        if isinstance(base.contents[0][0], urwid.Columns):
                            if i <= cur:
                                last_card_idx = acc
                            acc += 1
                    except Exception:
                        pass
        except Exception:
            pass
        return last_card_idx

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
        body_len = len(self.body) if hasattr(self.body, '__len__') else 0
        first_item_idx = self._get_actual_first() if hasattr(self, "_get_actual_first") else 0
        
        if self._scrollbar:
            if self._use_visual_total and (self._fixed_lines_per_item > 0) and self._count_only_pile_as_item:
                # 1) 카드 수 집계
                card_cnt = self._count_cards()

                # 2) [중요] visual_total을 “카드수 × 고정줄수”로 ‘정확히’ 잡음 (−1 보정 삭제)
                #    예: 카드 6개, 카드당 5줄 -> vtotal = 30
                vtotal = card_cnt * self._fixed_lines_per_item

                # 3) 현재 ‘카드 인덱스’ 근사
                cur_card = self._approx_first_card_index(first_item_idx)

                # 4) 한 화면에 들어가는 카드 수와 first 상한 계산
                avg_per_card = (vtotal / card_cnt) if card_cnt > 0 else 1.0   # 여기서는 고정값(=고정줄수)와 동일
                cards_per_view = maxrow / max(1e-9, avg_per_card)
                max_first_cards = max(0, int(math.ceil(card_cnt - cards_per_view)))

                # 5) 상한으로 클램핑된 virt_first
                virt_first = min(cur_card, max_first_cards)

                # 6) 가상 인덱스 모드 ON + 스크롤바에 전달(“스케일을 전부 카드 기준으로 통일”)
                self._virtual_index_mode = True
                self._scrollbar.update(
                    total=card_cnt,              # total = 카드 개수
                    first=virt_first,            # first = 카드 인덱스(상한 클램프)
                    height=maxrow,
                    visual_total=vtotal          # 썸 크기/비율 = ‘정확한’ 시각 줄 수
                )
            else:
                # 일반(Logs 등) 모드
                self._virtual_index_mode = False
                self._scrollbar.update(total=body_len, first=first_item_idx, height=maxrow)

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
            logger.debug(f"calculate_visible failed: {e}")
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
        if total <= 0: return
        new_focus = max(0, min(new_focus, total - 1))
        try: self.set_focus(new_focus)
        except: pass
        if update_selection and not self._lock_selection:
            self._apply_selection(new_focus)
        self._invalidate()

    def _scroll_view(self, delta: int):
        top_idx, cur_idx, bot_idx = self.get_view_indices()
        total = len(self.body) if hasattr(self.body, '__len__') else 0

        if total <= 0:
            logger.warning(f"[_scroll_view] Empty body, skipping")
            return
        
        self._update_sticky_from_current()

        base = top_idx if delta < 0 else bot_idx
        if base is None:
            try:
                base = int(self.focus_position)
            except:
                base = 0
        new_focus = max(0, min(int(base) + delta, total - 1))
        
        if new_focus != base:
            try:
                coming = 'above' if delta > 0 else 'below'
                self.set_focus(new_focus, coming_from=coming)
                logger.debug(f"[_scroll_view] Focus changed successfully")
            except Exception as e:
                logger.error(f"[_scroll_view] set_focus failed: {e}")
        else:
            logger.debug(f"[_scroll_view] Focus unchanged (boundary)")
        
        self._apply_sticky_inner_focus()
        self._invalidate()

    def mouse_event(self, size, event, button, col, row, focus):
        if event == 'mouse press' and button == 1:
            ret = super().mouse_event(size, event, button, col, row, focus)
            self._update_sticky_from_current()    # 사용자가 클릭한 칼럼을 sticky 로
            # 클릭은 '선택'의도 → 잠금과 무관하게 선택 갱신 허용
            if self._enable_selection:
                try: self._apply_selection(int(self.focus_position))
                except: pass
            return ret
        
    
        if event == 'mouse press' and button in (4, 5):
            delta = -1 if button == 4 else 1
            if getattr(self, "_virtual_index_mode", False):
                self._scroll_by_cards(delta_cards=delta)
                return True
            else:
                # 아이템(줄) 단위 스크롤 — 선택 잠금 반영
                top_idx, cur_idx, bot_idx = self.get_view_indices()
                total = len(self.body) if hasattr(self.body, '__len__') else 0
                if total <= 0: return True
                base = top_idx if delta < 0 else bot_idx
                if base is None:
                    try: base = int(self.focus_position)
                    except: base = 0
                new_focus = max(0, min(int(base) + delta, total - 1))
                # [핵심] 잠금 시 선택 유지
                self._navigate_to(new_focus, update_selection=not self._lock_selection)
                return True
        
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
                    except Exception as e:
                        pass
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
                    except Exception as e:
                        pass

            return res

        # 드래그 활성: drag/release를 선처리, 나머지 키만 원래 루프로
        new_keys = []
        for key in keys:
            if isinstance(key, tuple) and len(key) >= 4:
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
                        pass
                    continue

                if et == 'mouse release':
                    
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
                            pass
                    continue

            new_keys.append(key)

        return original_process(new_keys)

    loop.process_input = process_with_global_drag