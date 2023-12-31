# -*- coding: utf-8 -*-
"""ner_module.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1ymz6kRm0D3uB1uNQi4Nd8d836vUZ7v1t

# 모듈 다운로드
"""

###########################################################################################################
"""
ner_module.py : 추론 관련 코드.
- 실행하는 폴더에 predict.py, label.py, kpf-bert, kpf-bert-ner 폴더가 있어야함.
input : text (sentence)
output : word, label, desc (predict results by kpf-bert-ner)
"""
###########################################################################################################

import kss
from tqdm import tqdm
import pandas as pd
from transformers import AutoTokenizer, BertForTokenClassification, logging
logging.set_verbosity_error()
import sys, os
import numpy as np
sys.path.insert(0, '../')
import label
import json
import torch
from database import MysqlConnection
import re
from sklearn.metrics import jaccard_score
from itertools import combinations
from collections import Counter, defaultdict
from kiwipiepy import Kiwi

print("Torch version:{}".format(torch.__version__))
print("cuda version: {}".format(torch.version.cuda))
print("cudnn version:{}".format(torch.backends.cudnn.version()))

os.environ["CUDA_DEVICE_ORDER"]="PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"]="0"

torch.cuda.is_available()

!nvcc --version

from sklearn.metrics.pairwise import cosine_similarity

tokenizer = AutoTokenizer.from_pretrained("kpfbert")
model = BertForTokenClassification.from_pretrained("KPF/KPF-bert-ner")

def ner_predict(text):
    text = text.replace('\n','')
    model.to("cuda")

    sents = kss.split_sentences(text)
    decoding_ner_sentence = ""
    word_list = []
    pred_str = []

    #text to model input
    for idx, sent in enumerate(sents):

        sent = sent.replace(" ", "-")
        test_tokenized = tokenizer(sent, return_tensors="pt")

        test_input_ids = test_tokenized["input_ids"].to("cuda")
        test_attention_mask = test_tokenized["attention_mask"].to("cuda")
        test_token_type_ids = test_tokenized["token_type_ids"].to("cuda")

        inputs = {
            "input_ids" : test_input_ids,
            "attention_mask" : test_attention_mask,
            "token_type_ids" : test_token_type_ids
        }

        if inputs['input_ids'].size()[1] > 512:
            cnt = int(inputs['input_ids'].size()[1])

            inp_np = inputs['input_ids'].cpu().numpy()
            att_np = inputs['attention_mask'].cpu().numpy()
            tok_np = inputs['token_type_ids'].cpu().numpy()

            for i in range(cnt):
                slice_inp = inp_np[0][(i*512):((i+1)*512)]
                slice_att = att_np[0][(i * 512):((i + 1) * 512)]
                slice_tok = tok_np[0][(i * 512):((i + 1) * 512)]

                slice_inp = slice_inp.reshape(1, len(slice_inp))
                slice_att = slice_att.reshape(1, len(slice_att))
                slice_tok = slice_tok.reshape(1, len(slice_tok))

                slice_inp = torch.tensor(slice_inp)
                slice_att = torch.tensor(slice_att)
                slice_tok = torch.tensor(slice_tok)

                slice_inp = torch.tensor(slice_inp).to("cuda")
                slice_att = torch.tensor(slice_att).to("cuda")
                slice_tok = torch.tensor(slice_tok).to("cuda")

                slice_inputs = {
                    "input_ids": slice_inp,
                    "attention_mask": slice_att,
                    "token_type_ids": slice_tok
                }

                # predict
                outputs = model(**slice_inputs)
                token_predictions = outputs[0].argmax(dim=2)
                token_prediction_list = token_predictions.squeeze(0).tolist()

                pred = [label.id2label[l] for l in token_prediction_list]
                pred_str = np.concatenate((pred_str, pred))
        else:
            #predict
            outputs = model(**inputs)
            token_predictions = outputs[0].argmax(dim=2)
            token_prediction_list = token_predictions.squeeze(0).tolist()

            pred_str = [label.id2label[l] for l in token_prediction_list]
        tt_tokenized = tokenizer(sent).encodings[0].tokens


        # decoding_ner_sentence = ""
        is_prev_entity = False
        prev_entity_tag = ""
        is_there_B_before_I = False
        _word = ""
        # word_list = list()

        #model output to text
        for i, (token, pred) in enumerate(zip(tt_tokenized, pred_str)):
            if i == 0 or i == len(pred_str) - 1:
                continue
            token = token.replace('#', '').replace("-", " ")

            if token == "":
                continue

            if 'B-' in pred:
                if is_prev_entity is True:
                    decoding_ner_sentence += ':' + prev_entity_tag+ '>'
                    word_list.append({"word" : _word, "label" : prev_entity_tag, "desc" : "1", "bio": f"B-{prev_entity_tag}"})  # Add 'bio' key
                    _word = ""

                if token[0] == ' ':
                    token = list(token)
                    token[0] = ' <'
                    token = ''.join(token)
                    decoding_ner_sentence += token
                    _word += token
                else:
                    decoding_ner_sentence += '<' + token
                    _word += token
                is_prev_entity = True
                prev_entity_tag = pred[2:]
                is_there_B_before_I = True

            elif 'I-' in pred:
                decoding_ner_sentence += token
                _word += token


                if is_there_B_before_I is True:
                    is_prev_entity = True

            else:
                if is_prev_entity is True:
                    decoding_ner_sentence += ':' + prev_entity_tag+ '>' + token
                    is_prev_entity = False
                    decoding_ner_sentence += ':' + prev_entity_tag+ '>' + token
                    is_prev_entity = False
                    is_there_B_before_I = False
                    word_list.append({"word" : _word, "label" : prev_entity_tag, "desc" : label.ner_code[prev_entity_tag], "bio": f"I-{prev_entity_tag}"})  # Add 'bio' key for 'I-' tag
                    _word = ""
                else:
                    decoding_ner_sentence += token


    return word_list

"""# NER실행 및 동의어 딕셔너리 생성"""

db_connection = MysqlConnection()
conn = db_connection.connection
cursor = conn.cursor()

target_date = "2023-08-23"
query = f"SELECT nc_id, nid FROM kordata.news_cluster WHERE datetime = '{target_date}'"
cursor.execute(query)
result = cursor.fetchall()

num_dicts = []
for idx, row in enumerate(result):
    nc_id, nid_string = row
    nids = [int(num) for num in re.findall(r'\d+', nid_string)]
    num_dict = {'datetime': target_date, 'nc_id': nc_id, 'nids': nids, 'order': idx + 1, 'articles': []}
    num_dicts.append(num_dict)

list_summary=[]
list_main_text=[]
for num_dict in num_dicts:
    for nid in num_dict['nids']:
        query = "SELECT nid, main_text, summary FROM news WHERE nid = %s"
        value = (nid, )
        cursor.execute(query, value)
        article_result = cursor.fetchall()
        if article_result:
            article_info = {
                'nid': article_result[0][0],
                'main_text': article_result[0][1],
                'summary': article_result[0][2]
            }
            num_dict['articles'].append(article_info)

    for one in num_dict['articles']:
        list_summary.append(one['summary'])

    for one in num_dict['articles']:
      nid = one['nid']
      main_text = one['main_text']
      entry = {'nid': nid, 'main_text': main_text}
      list_main_text.append(entry)

list_main_text

real_ans=[]
real_nid_list=[]
for i in num_dicts:
  filtered_data=i['articles']
  nc_id=i['nc_id']
  datetime=i['datetime']
  sum_tot = []
  nid_list=[]
  for d in tqdm(filtered_data):
    res = ner_predict(d['summary'])
    nid_list.append(d["nid"])
    for k in res:
      k['datetime']=datetime
      k['nid']=d['nid']
      k["nc_id"]= nc_id

    sum_tot.append(res)
  real_ans.append(sum_tot)
  real_nid_list.append(nid_list)

filtered_data = [
    [
        [
            item for item in sublist2 if not (item['label'].startswith('DT') or item['label'].startswith('TI') or item['label'].startswith('LCP'))
        ]
        for sublist2 in sublist1
    ]
    for sublist1 in real_ans
]

data=filtered_data

data

filtered_entities = []

for article_data in data:
    for sentence_data in article_data:
        k = 0
        while k < len(sentence_data) - 1:
            if sentence_data[k]['bio'] == 'I-PS_NAME' and sentence_data[k+1]['bio'] == 'I-CV_POSITION':
                filtered_entities.append({
                    'word': sentence_data[k]['word'] + ' ' + sentence_data[k+1]['word'],
                    'label': sentence_data[k]['label'],
                    'desc': sentence_data[k]['desc'],
                    'bio': 'I-PS_NAME',
                    'datetime': sentence_data[k]['datetime'],
                    'nid': sentence_data[k]['nid'],
                    'nc_id': sentence_data[k]['nc_id']
                })
                k += 2
            elif sentence_data[k]['label'].startswith('OGG'):
                filtered_entities.append({
                    'word': sentence_data[k]['word'],
                    'label': sentence_data[k]['label'],
                    'desc': sentence_data[k]['desc'],
                    'bio': sentence_data[k]['bio'],
                    'datetime': sentence_data[k]['datetime'],
                    'nid': sentence_data[k]['nid'],
                    'nc_id': sentence_data[k]['nc_id']
                })
                k += 1
            else:
                k += 1

merged_entities=filtered_entities

result_list=merged_entities



# 자카드 유사도 계산 함수
def calculate_jaccard_similarity(set1, set2):
    intersection = len(set1.intersection(set2))
    union = len(set1.union(set2))
    return intersection / union if union != 0 else 0.0

# 결과를 담을 딕셔너리
result_dict = {}

# 자카드 유사도 계산 및 딕셔너리에 추가
for i in range(len(result_list)):
    word1 = set(result_list[i]['word'])
    similar_words = [(result['word'], set(result['word'])) for result in result_list if result['word'] != word1]
    similar_words = [(word, set_word) for word, set_word in similar_words if calculate_jaccard_similarity(word1, set_word) > 0.6 and calculate_jaccard_similarity(word1, set_word) < 1.0]

    if not similar_words:
        continue

    similar_word_pairs = {result_list[i]['word']: word for word, set_word in similar_words}
    result_dict.update(similar_word_pairs)

filtered_data = {}

for key, value in result_dict.items():
    if value in result_dict and result_dict[value] == key:
        filtered_data[key] = value

filtered_data_fin = {}

for key, value in filtered_data.items():
    if key < value:  # 알파벳 순서가 더 작은 키만 남기기
        if value not in filtered_data_fin or key < filtered_data_fin[value]:
            filtered_data_fin[value] = key

reversed_filtered_data_fin = {v: k for k, v in filtered_data_fin.items()}

def update_synonym_dict_with_summaries(synonym_dict, list_summary):
    updated_synonym_dict = synonym_dict.copy()

    for key, value in synonym_dict.items():
        if any(value in summary for summary in list_summary):
            updated_synonym_dict[key] = value
        else:
            updated_synonym_dict[key] = key  # 서머리에 해당 단어가 없을 경우 key로 대체

    return updated_synonym_dict

synonym_dict = reversed_filtered_data_fin

updated_synonym_dict = update_synonym_dict_with_summaries(synonym_dict, list_summary)

reversed_filtered_data_fin=updated_synonym_dict

reversed_filtered_data_fin

"""# 클러스터별 상위 5개 포멧에 맞춰 DB에 저장"""

data=result_list

cluster_word_counts = defaultdict(Counter)
for entry in data:
    cluster_word_counts[entry['nc_id']].update({entry['word']: 1})

result_list = []

for nc_id, word_counts in cluster_word_counts.items():
    most_common_words = [word for word, _ in word_counts.most_common(5)]
    for word in most_common_words:
        word_entries = [entry for entry in data if entry['nc_id'] == nc_id and entry['word'] == word]
        seen_nids = set()  # 이미 처리한 기사 ID 저장
        for entry in word_entries:
            # 이미 처리한 기사 ID인 경우 skip
            if entry['nid'] in seen_nids:
                continue
            result_dict = {
                'word': word,
                'nc_id': nc_id,
                'label': entry['label'],
                'desc': entry['desc'],
                'nid': entry['nid'],
                'datetime': entry['datetime']
            }
            result_list.append(result_dict)
            seen_nids.add(entry['nid'])  # 처리한 기사 ID 추가

df = pd.DataFrame(result_list)

synonym_dict = reversed_filtered_data_fin

df['main_word'] = df['word'].apply(lambda word: synonym_dict[word] if word in synonym_dict else word)

df


db_connection = MysqlConnection()
conn = db_connection.connection
cursor = conn.cursor()

table_name = 'entity'

sql = f"INSERT INTO {table_name} (word, nc_id, label, `desc`, nid, datetime, main_word) VALUES (%s, %s, %s, %s, %s, %s, %s)"
values = [(row['word'], row['nc_id'], row['label'], row['desc'], row['nid'], row['datetime'], row['main_word']) for index, row in df.iterrows()]
cursor.executemany(sql, values)

conn.commit()
cursor.close()
conn.close()

print(f"Data has been successfully inserted into the '{table_name}' table.")

"""클러스터별로 nc_id를 부여받음

# 주체 관련 문장 추출 DB에 저장
"""


# MySQL 연결 객체 생성
db_connection = MysqlConnection()
conn = db_connection.connection
cursor = conn.cursor()

# 테이블 이름 설정
table_name = 'entity'

# 해당 날짜의 데이터만 가져와 데이터프레임 생성
query = f"SELECT nid, eid, word, datetime FROM {table_name} WHERE datetime = %s"
df = pd.read_sql_query(query, db_connection, params=[target_date])

# MySQL 연결 종료
conn.close()

# 결과 출력
df


kiwi = Kiwi()

data = list_main_text

# 결과를 저장할 딕셔너리 초기화
result_dict = {}

# tqdm으로 루프 감싸기
for item in tqdm(data, desc="Processing", leave=False):
    nid = item['nid']
    main_text = item['main_text']

    # kiwi를 사용하여 문장 분리
    result = kiwi.split_into_sents(main_text)

    # 'text'만 추출하여 리스트로 저장
    sentences = [sentence.text for sentence in result]

    # nid와 문장 리스트를 딕셔너리에 추가
    result_dict[nid] = sentences

# 딕셔너리 예시
dictionary = result_dict

# 'nid' 값을 사용하여 딕셔너리에서 값을 가져와 'sent' 컬럼에 넣기
df['sent'] = df['nid'].apply(lambda x: dictionary.get(x, []))

# 'word'와 'sent'의 각 문장들을 행으로 나눠 새로운 데이터프레임 생성
new_rows = []
for idx, row in df.iterrows():
    word = row['word']
    sentences = row['sent']
    for sentence in sentences:
        new_rows.append({
            'eid': row['eid'],
            'nid': row['nid'],
            'datetime': row['datetime'],
            'sentence': sentence
        })

# 새로운 데이터프레임 생성
new_df = pd.DataFrame(new_rows)

# 결과 출력
new_df


db_connection = MysqlConnection()
conn = db_connection.connection
cursor = conn.cursor()

# 테이블 이름 설정
table_name = 'sentence'

# 데이터프레임을 MySQL 테이블에 추가
sql = f"INSERT INTO {table_name} (eid, nid, datetime,sentence) VALUES (%s, %s, %s, %s)"
values = [(row['eid'], row['nid'], row['datetime'], row['sentence']) for index, row in new_df.iterrows()]
cursor.executemany(sql, values)

# 변경사항 저장 및 커넥션 종료
conn.commit()
cursor.close()
conn.close()

print(f"Data has been successfully inserted into the '{table_name}' table.")

