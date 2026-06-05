# GraphRAG 간단 실습

LangChain, OpenAI, Neo4j를 사용해 한국어 문장에서 간단한 지식 그래프를 만들고, 그래프를 기반으로 질문하는 GraphRAG 실습 코드입니다.

## 사전 준비
1. Neo4j Desktop 설치 : https://incongruous-modem-465.notion.site/Neo4j-Desktop-3769cc89b5d980d7b95dd68f32417076?source=copy_link

## 실습 흐름

1. Neo4j 연결을 확인합니다.
2. 한국어 문장에서 엔티티와 관계를 추출 및 Neo4j 그래프로 저장합니다
3. 저장된 그래프를 대상으로 자연어 질문을 실행합니다.

## 파일 구성

| 파일 | 설명 |
| --- | --- |
| `1_test_connection.py` | `.env`에 설정한 Neo4j 접속 정보로 연결을 테스트합니다. |
| `2_build_graph.py` | OpenAI 모델로 문장에서 지식 그래프를 추출하고 Neo4j에 저장합니다. |
| `3_ask_graph.py` | Neo4j 그래프 스키마를 읽고 `GraphCypherQAChain`으로 질문에 답합니다. |
| `requirements.txt` | 현재 실습 환경의 패키지 버전 목록입니다. |

## 준비 사항

- Python 3.10 이상 권장
- OpenAI API 키
- Neo4j Desktop 앱

## 패키지 설치

가상환경을 만든 뒤 필요한 패키지를 설치합니다.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U langchain langchain-openai langchain-neo4j python-dotenv
```

이미 `requirements.txt` 기준으로 동일한 환경을 맞추고 싶다면 다음 명령을 사용할 수도 있습니다.

```bash
pip install -r requirements.txt
```

macOS의 경우
```bash
pip install -r requirements_macos.txt
```

## 환경변수 설정

프로젝트 루트에 `.env` 파일을 만들고 아래 값을 채웁니다.

```env
OPENAI_API_KEY=your_openai_api_key
NEO4J_URI=neo4j+s://your-neo4j-host
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_neo4j_password
```

필요하면 모델명과 데이터베이스 이름도 추가할 수 있습니다.

```env
OPENAI_MODEL=gpt-5.5
NEO4J_DATABASE=neo4j
```

`OPENAI_MODEL`을 지정하지 않으면 코드에서는 기본값으로 `gpt-5.5`를 사용합니다.

## 실행 방법

### 1. Neo4j 연결 테스트

```bash
python 1_test_connection.py
```

정상 연결되면 다음 메시지가 출력됩니다.

```text
Neo4j 연결 성공!
```

### 2. 지식 그래프 생성

```bash
python 2_build_graph.py
```

이 스크립트는 아래 문장에서 노드와 관계를 추출합니다.

```text
김민수는 결제 시스템 리팩터링을 담당했다.
결제 시스템 리팩터링은 장애율을 낮추기 위한 프로젝트였다.
결제 시스템 리팩터링은 보안팀과 플랫폼팀이 공동으로 진행했다.
```

추출된 그래프는 Neo4j에 `Entity` 노드로 저장되며, 타입에 따라 `Person`, `Project`, `Team`, `Metric`, `System`, `Unknown` 라벨이 추가됩니다.

생성되는 관계 타입은 다음과 같습니다.

| 관계 | 의미 |
| --- | --- |
| `RESPONSIBLE_FOR` | 사람이 프로젝트나 업무를 담당함 |
| `AIMS_TO_REDUCE` | 프로젝트가 어떤 지표를 낮추는 목적을 가짐 |
| `COLLABORATED_ON` | 팀이 프로젝트를 공동 진행함 |
| `RELATED_TO` | 기타 일반 관계 |

### 3. 그래프에 질문하기

```bash
python 3_ask_graph.py
```

현재 예제 질문은 다음과 같습니다.

```text
김민수와 보안팀은 어떤 관계야?
```

`GraphCypherQAChain`이 Neo4j 스키마를 바탕으로 Cypher 쿼리를 생성하고, 그래프 조회 결과를 자연어 답변으로 정리합니다.

## Neo4j에서 확인하기

Neo4j Desktop에서 다음 Cypher로 생성된 그래프를 확인할 수 있습니다.

```cypher
MATCH (n)-[r]->(m)
RETURN n, r, m
```

전체 노드만 보고 싶다면 다음 쿼리를 실행합니다.

```cypher
MATCH (n)
RETURN n
```
