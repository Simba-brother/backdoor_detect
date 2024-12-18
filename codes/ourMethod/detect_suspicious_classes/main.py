'''
检测target class
'''
import os
from codes import config
import torch
import numpy as np
from scipy import stats
from cliffs_delta import cliffs_delta
import joblib
import logging
import setproctitle
# https://github.com/klainfo/ScottKnottESD
import rpy2.robjects.packages as rpackages
from rpy2.robjects.vectors import StrVector
from rpy2.robjects.packages import importr
from rpy2.robjects import r, pandas2ri
from collections import defaultdict
import pandas as pd


def calu_p_and_dela_value(list_1, list_2):
    p_value = stats.wilcoxon(list_1, list_2).pvalue
    list_1_sorted = sorted(list_1) # 原来list不改变
    list_2_sorted = sorted(list_2)
    delta,info = cliffs_delta(list_1_sorted, list_2_sorted)
    return p_value,delta


def get_suspicious_classes(data_dict):
    '''
    data_dict:{Int(class_idx):list(precision|recall|F1)}
    '''
    # Rule_1:Wilcoxon Signed Rank Test and Cliff's Delta
    rule_1_classes = set()
    for i in range(config.class_num):
        # 当前类别i得指标与其他指标的Wilcoxon rank sum test p值
        p_list = []
        # 当前类别i得指标与其他指标的Cliff’s delta 值
        delta_list = []
        for j in range(config.class_num):
            if j == i:
                continue
            p_value,delta = calu_p_and_dela_value(data_dict[i],data_dict[j])
            p_list.append(p_value)
            delta_list.append(delta)
        all_P_flag = all(p_value < 0.05 for p_value in p_list)
        all_C_flag = all(d > 0.147 for d in delta_list)
        if all_P_flag and all_C_flag:
            # i类别指标分布与其他类别显著有区别且值较大
            rule_1_classes.add(i)
    
    # Rule_2:均值最大类
    rule_2_classes = set()
    max_avg = -1
    max_avg_class_idx = -1
    for i in range(config.class_num):
        array = np.array(data_dict[i])
        # 当前类别i的指标均值
        avg_value = np.round(np.mean(array),decimals=4).item()
        if avg_value > max_avg:
            max_avg = avg_value
            max_avg_class_idx = i
    rule_2_classes.add(max_avg_class_idx)

    # Rule_3:中位值最大类
    rule_3_classes = set()
    max_mid = -1
    max_mid_class_idx = -1
    for i in range(config.class_num):
        array = np.array(data_dict[i])
        # 当前类别i的指标均值
        mid_value = np.round(np.median(array),decimals=4).item()
        if mid_value > max_mid:
            max_mid = mid_value
            max_mid_class_idx = i
    rule_3_classes.add(max_mid_class_idx)

    suspicious_classes = rule_1_classes | rule_2_classes | rule_3_classes
    return suspicious_classes

def get_suspicious_classes_by_ScottKnottESD(data_dict):
    '''
    data_dict:{Int(class_idx):list(precision|recall|F1)}
    '''
    pandas2ri.activate()
    sk = importr("ScottKnottESD")
    df = pd.DataFrame(data_dict)
    r_sk = sk.sk_esd(df)
    column_order = [x-1 for x in list(r_sk[3])]

    ranking = pd.DataFrame(
        {
            "Class": [df.columns[i] for i in column_order],
            "rank": r_sk[1].astype("int"),
        })
    Class_list = list(ranking["Class"])
    rank_list = list(ranking["rank"])
    group_map = defaultdict(list)
    for class_idx, rank in zip(Class_list,rank_list):
        group_map[rank].append(class_idx)
    group_key_list = list(group_map.keys())
    group_key_list.sort() # replace
    top_key = group_key_list[0]
    suspicious_classes = group_map[top_key]
    return suspicious_classes
    


def reconstruct_data(report_dataset,measure_name):
    '''
    args:
        report_dataset:
            {
                ratio:{
                    operation:[report_classification]
                }
            }
        measure_name:str,precision|recall|f1-score
    return:
        {
            ratio:{
                class_id:[measure]
            }
        }
    '''
    data = {}
    for ratio in config.fine_mutation_rate_list:
        data[ratio] = {}
        for class_i in range(config.class_num):
            data[ratio][class_i] = []
            for op in config.mutation_name_list:
                for report in report_dataset[ratio][op]:
                    data[ratio][class_i].append(report[str(class_i)][measure_name])
    return data

def detect(report_dataset):
    '''
    args:
        report_dataset:
            {
                ratio:{
                    operation:[report_classification]
                }
            }
    return:
        {
            ratio:target class
        }
    '''
    ans = {}
    data = reconstruct_data(report_dataset,measure_name="precision") 
    for ratio in config.fine_mutation_rate_list:
        suspicious_classes = get_suspicious_classes_by_ScottKnottESD(data[ratio])
        ans[ratio] = suspicious_classes
    return ans



if __name__ == "__main__":
    # 进程名称
    proctitle = f"SuspiciousClasses_ScottKnottESD_Precision|{config.dataset_name}|{config.model_name}|{config.attack_name}"
    setproctitle.setproctitle(proctitle)
    device = torch.device("cuda:0")

    # 日志保存目录
    LOG_FORMAT = "时间：%(asctime)s - 日志等级：%(levelname)s - 日志信息：%(message)s"
    LOG_FILE_DIR = os.path.join("log",config.dataset_name,config.model_name,config.attack_name)
    os.makedirs(LOG_FILE_DIR,exist_ok=True)
    LOG_FILE_NAME = "SuspiciousClasses_ScottKnottESD_Precision.log"
    LOG_FILE_PATH = os.path.join(LOG_FILE_DIR,LOG_FILE_NAME)
    logging.basicConfig(level=logging.DEBUG,format=LOG_FORMAT,filename=LOG_FILE_PATH,filemode="w")
    logging.debug(proctitle)

    # 加载变异模型评估结果
    evalMutationResult = joblib.load(os.path.join(
        config.exp_root_dir,
        "EvalMutationResult",
        config.dataset_name, 
        config.model_name, 
        config.attack_name,
        "EvalMutationResult.data"
    ))
    # 得到各个变异率下的target class
    target_class_ans = detect(evalMutationResult)
    logging.debug(target_class_ans)
    # 保存实验结果
    save_dir = os.path.join(
        config.exp_root_dir,
        "TargetClass",
        config.dataset_name, 
        config.model_name, 
        config.attack_name
    )
    os.makedirs(save_dir,exist_ok=True)
    save_file_name = "SuspiciousClasses_ScottKnottESD_Precision.data"
    save_file_path = os.path.join(save_dir,save_file_name)
    joblib.dump(target_class_ans,save_file_path)
    logging.debug(f"target class结果保存在:{save_file_path}")


    