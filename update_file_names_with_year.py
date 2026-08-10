import os
import re
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

data_dir = os.path.join(os.path.dirname(__file__), "data")

if not os.path.exists(data_dir):
    print(f"Error: Data directory not found: {data_dir}")
    sys.exit(1)

files = [f for f in os.listdir(data_dir) if f.endswith(".md")]

print(f"Found {len(files)} markdown files in data directory.")

def extract_year(filename, content):
    # 1. 파일명에서 연도 패턴 검사
    # 예: 2023년, 2025회계연도, 2026년, 20150506, (2019년), 230601, 240503, 161219, 130821, 170720, 220406, 250804
    m = re.search(r'(20\d{2})년?', filename)
    if m:
        return m.group(1)
    
    m_yy = re.search(r'(\d{2})(\d{2})(\d{2})', filename)
    if m_yy:
        yy = int(m_yy.group(1))
        if 10 <= yy <= 30:
            return f"20{yy:02d}"
        elif 90 <= yy <= 99:
            return f"19{yy:02d}"

    m_yy2 = re.search(r'(\d{2})\.\d{2}\.\d{2}', filename)
    if m_yy2:
        yy = int(m_yy2.group(1))
        if 10 <= yy <= 30:
            return f"20{yy:02d}"

    # 2. 본문 내용에서 연도 패턴 검색 (상단 2000자 이내)
    sample = content[:2500]
    
    # 20XX년, 20XX회계연도, 20XX.XX.XX
    years = re.findall(r'(20[0-2][0-9])', sample)
    if years:
        # 가장 많이 등장하거나 최신으로 추정되는 연도
        from collections import Counter
        most_common = Counter(years).most_common(1)[0][0]
        return most_common

    return None

renamed_count = 0
already_formatted = 0

for filename in files:
    filepath = os.path.join(data_dir, filename)
    
    # 이미 [YYYY년] 형식이 되어있는지 확인
    if re.match(r'^\[20\d{2}년\]', filename):
        already_formatted += 1
        continue
        
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"[WARN] Failed to read {filename}: {e}")
        continue

    year = extract_year(filename, content)
    
    if not year:
        # 본문 전체에서 다시 수색
        years = re.findall(r'(20[0-2][0-9])', content)
        if years:
            from collections import Counter
            year = Counter(years).most_common(1)[0][0]
        else:
            year = "2024" # 기본값 2024년 설정

    new_filename = f"[{year}년] {filename}"
    new_filepath = os.path.join(data_dir, new_filename)
    
    try:
        os.rename(filepath, new_filepath)
        print(f"Renamed: '{filename}' -> '{new_filename}'")
        renamed_count += 1
    except Exception as e:
        print(f"[ERROR] Could not rename '{filename}': {e}")

print(f"\nDone! Renamed {renamed_count} files ({already_formatted} files were already formatted).")
