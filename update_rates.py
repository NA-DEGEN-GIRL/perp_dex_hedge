#!/usr/bin/env python3
"""
config.ini.example의 *_rate 값을 config.ini에 업데이트하는 스크립트.
- builder_code가 동일한 섹션만 업데이트
- 예외: hyena 섹션은 섹션 이름으로 매칭
- 기존 *_rate 옵션 삭제 후 example의 rate 옵션으로 교체
"""

import re
from pathlib import Path

# 섹션 이름으로 매칭하는 예외 목록
SECTION_NAME_MATCH = {"hyena"}


def parse_config(lines: list) -> dict:
    """
    config 파일을 파싱하여 섹션별 정보 추출.
    Returns: {section: {"builder_code": str, "rates": {key: value}, "line_indices": [...]}}
    """
    result = {}
    current_section = None
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        
        # 섹션 감지
        section_match = re.match(r'^\[([^\]]+)\]', stripped)
        if section_match:
            current_section = section_match.group(1).strip()
            result[current_section] = {
                "builder_code": None,
                "rates": {},
                "rate_line_indices": [],
                "section_start": i,
                "builder_code_line": None,
            }
            continue
        
        if not current_section:
            continue
        
        # builder_code 감지
        bc_match = re.match(r'^builder_code\s*=\s*(.+)$', stripped)
        if bc_match:
            result[current_section]["builder_code"] = bc_match.group(1).strip()
            result[current_section]["builder_code_line"] = i
            continue
        
        # *_rate 감지
        rate_match = re.match(r'^(\w+_rate)\s*=\s*(.+)$', stripped)
        if rate_match:
            key = rate_match.group(1)
            value = rate_match.group(2).strip()
            result[current_section]["rates"][key] = value
            result[current_section]["rate_line_indices"].append(i)
    
    return result


def find_matching_example_section(
    target_section: str,
    target_info: dict,
    example_data: dict,
    bc_to_example: dict
) -> str | None:
    """
    target 섹션에 매칭되는 example 섹션 찾기.
    - hyena 등 SECTION_NAME_MATCH에 있으면 섹션 이름으로 매칭
    - 그 외는 builder_code로 매칭
    """
    # 1. 섹션 이름 매칭 (예외)
    if target_section.lower() in SECTION_NAME_MATCH:
        # example에서 같은 이름의 섹션 찾기
        for example_section in example_data:
            if example_section.lower() == target_section.lower():
                return example_section
        return None
    
    # 2. builder_code 매칭 (일반)
    target_bc = target_info.get("builder_code")
    if target_bc:
        return bc_to_example.get(target_bc)
    
    return None


def update_rates(example_path: Path, target_path: Path, dry_run: bool = False):
    """
    builder_code가 동일한 섹션에 대해 *_rate 값을 업데이트.
    hyena 섹션은 섹션 이름으로 매칭.
    """
    # 파일 읽기
    with open(example_path, "r", encoding="utf-8") as f:
        example_lines = f.readlines()
    
    with open(target_path, "r", encoding="utf-8") as f:
        target_lines = f.readlines()
    
    # 파싱
    example_data = parse_config(example_lines)
    target_data = parse_config(target_lines)
    
    # builder_code → example 섹션 매핑
    bc_to_example = {}
    for section, data in example_data.items():
        bc = data.get("builder_code")
        if bc:
            bc_to_example[bc] = section
    
    # 업데이트할 섹션 찾기
    updates = []  # [(target_section, example_section, old_rates, new_rates, target_info, match_type)]
    
    for target_section, target_info in target_data.items():
        # 매칭되는 example 섹션 찾기
        example_section = find_matching_example_section(
            target_section, target_info, example_data, bc_to_example
        )
        
        if not example_section:
            continue
        
        example_info = example_data.get(example_section, {})
        old_rates = target_info.get("rates", {})
        new_rates = example_info.get("rates", {})
        
        # 매칭 타입 결정
        match_type = "섹션명" if target_section.lower() in SECTION_NAME_MATCH else "builder_code"
        
        # 변경 있는지 확인
        if old_rates != new_rates:
            updates.append((target_section, example_section, old_rates, new_rates, target_info, match_type))
    
    if not updates:
        print("\n변경할 섹션이 없습니다.\n")
        return
    
    # 변경 내용 출력
    print(f"\n{'='*60}")
    print(f"총 {len(updates)}개 섹션 업데이트:")
    print(f"{'='*60}")
    
    for target_section, example_section, old_rates, new_rates, _, match_type in updates:
        print(f"\n[{target_section}] ({match_type} 매칭: [{example_section}])")
        
        # 삭제될 rate
        for key in old_rates:
            if key not in new_rates:
                print(f"  - {key} = {old_rates[key]} (삭제)")
            elif old_rates[key] != new_rates[key]:
                print(f"  ~ {key} = {old_rates[key]} → {new_rates[key]}")
            else:
                print(f"    {key} = {old_rates[key]} (유지)")
        
        # 추가될 rate
        for key in new_rates:
            if key not in old_rates:
                print(f"  + {key} = {new_rates[key]} (추가)")
    
    print(f"\n{'='*60}\n")
    
    if dry_run:
        print("(dry-run 모드: 실제로 저장하지 않음)")
        return
    
    # 새 파일 내용 생성
    # 삭제할 라인 인덱스 수집
    lines_to_remove = set()
    for _, _, _, _, target_info, _ in updates:
        lines_to_remove.update(target_info.get("rate_line_indices", []))
    
    # 라인별 처리
    new_lines = []
    current_section = None
    rate_inserted = set()  # 이미 rate를 삽입한 섹션
    
    # 업데이트 대상 섹션과 정보 맵
    update_map = {item[0]: item for item in updates}  # target_section → update tuple
    
    for i, line in enumerate(target_lines):
        stripped = line.strip()
        
        # 섹션 감지
        section_match = re.match(r'^\[([^\]]+)\]', stripped)
        if section_match:
            current_section = section_match.group(1).strip()
            new_lines.append(line)
            continue
        
        # 삭제할 라인이면 스킵
        if i in lines_to_remove:
            continue
        
        # 이 섹션이 업데이트 대상인지 확인
        if current_section and current_section in update_map and current_section not in rate_inserted:
            target_info = update_map[current_section][4]
            new_rates = update_map[current_section][3]
            
            # builder_code 라인 뒤 또는 섹션 시작 직후에 rate 삽입
            bc_line = target_info.get("builder_code_line")
            section_start = target_info.get("section_start")
            
            # builder_code가 있으면 그 뒤에, 없으면 섹션 시작 바로 뒤에 삽입
            insert_after = bc_line if bc_line is not None else section_start
            
            if i == insert_after:
                new_lines.append(line)
                # 새 rate 삽입
                for key, value in new_rates.items():
                    new_lines.append(f"{key} = {value}\n")
                rate_inserted.add(current_section)
                continue
        
        new_lines.append(line)
    
    # 저장
    with open(target_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    
    print(f"✅ {target_path} 업데이트 완료!")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="config.ini.example의 *_rate 값을 config.ini에 업데이트 (builder_code 매칭, hyena는 섹션명 매칭)"
    )
    parser.add_argument("--dry-run", "-n", action="store_true", help="변경 내용만 출력하고 저장하지 않음")
    parser.add_argument("--example", default="config.ini.example", help="example 파일 경로")
    parser.add_argument("--target", default="config.ini", help="대상 파일 경로")
    args = parser.parse_args()
    
    example_path = Path(args.example)
    target_path = Path(args.target)
    
    if not example_path.exists():
        print(f"❌ {example_path} 파일이 없습니다.")
        return
    
    if not target_path.exists():
        print(f"❌ {target_path} 파일이 없습니다.")
        return
    
    update_rates(example_path, target_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()