#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import time
import os
import sys
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--f") # ts, debug
args = parser.parse_args()

LOG_FILE = f'{args.f}.log'

def tail_f(filename):
    print(f"--- [Log Viewer] Watching: {filename} ---")
    print("Waiting for logs... (Press Ctrl+C to exit)")
    
    # 파일이 생길 때까지 대기
    while not os.path.exists(filename):
        time.sleep(0.5)

    try:
        with open(filename, 'r', encoding='utf-8', errors='replace') as f:
            # 파일의 끝으로 이동 (이미 쌓인 로그를 무시하고 싶다면 아래 주석 해제)
            # f.seek(0, 2) 
            
            # 처음부터 다 보고 싶으면 seek 없이 시작
            
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.1)  # 새로운 데이터가 없으면 잠시 대기
                    continue
                
                # 로그 출력 (줄바꿈 중복 방지)
                print(line, end='' if line.endswith('\n') else '\n')
                
                # 즉시 출력 (버퍼링 방지)
                sys.stdout.flush()
                
    except FileNotFoundError:
        print(f"\nError: File {filename} not found.")
    except Exception as e:
        print(f"\nError: {e}")

if __name__ == "__main__":
    try:
        # 화면 지우기 (선택사항)
        os.system('cls' if os.name == 'nt' else 'clear')
        tail_f(LOG_FILE)
    except KeyboardInterrupt:
        print("\n[Log Viewer] Stopped.")