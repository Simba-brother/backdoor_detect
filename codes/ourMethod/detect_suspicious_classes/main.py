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
from rpy2.robjects.packages import importr
from rpy2.robjects import pandas2ri
from collections import defaultdict
import pandas as pd
from collections import defaultdict
from codes.utils import entropy
from sklearn.metrics import accuracy_score, precision_score, f1_score, recall_score, classification_report,confusion_matrix



def calu_p_and_dela_value(list_1, list_2):
    p_value = stats.wilcoxon(list_1, list_2).pvalue
    list_1_sorted = sorted(list_1) # 原来list不改变
    list_2_sorted = sorted(list_2)
    delta,info = cliffs_delta(list_1_sorted, list_2_sorted)
    return p_value,delta


def get_suspicious_classes_by_Rule(data_dict):
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
    low_key = group_key_list[-1]
    suspicious_classes_top = group_map[top_key]
    suspicious_classes_low = group_map[low_key]
    return suspicious_classes_top,suspicious_classes_low
    


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

def detect(report_dataset,measure_name):
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
    data = reconstruct_data(report_dataset,measure_name)
    
    box_data_save_dir = os.path.join(config.exp_root_dir,"SK",config.dataset_name,config.model_name,config.attack_name)
    
    for ratio in config.fine_mutation_rate_list:
        save_dir = os.path.join(box_data_save_dir,str(ratio))
        os.makedirs(save_dir,exist_ok=True)
        save_file_name = f"box_{measure_name}.csv"
        save_path = os.path.join(save_dir,save_file_name)
        df = pd.DataFrame(data[ratio])
        # 直接设置新的列名列表
        new_col_name_list = []
        for old_col_name in list(df.columns):
            new_col_name_list.append("C"+str(old_col_name))
        df.columns = new_col_name_list
        df.to_csv(save_path,index=False)

    for ratio in config.fine_mutation_rate_list:
        suspicious_classes= get_suspicious_classes_by_ScottKnottESD(data[ratio])
        ans[ratio] = suspicious_classes
    return ans


def main_v1(measure_name):
    '''
    measure_name: precison | recall | f1-score
    '''
    # 加载变异模型评估结果
    evalMutationResult = joblib.load(os.path.join(
        config.exp_root_dir,
        "EvalMutationResult_for_SuspiciousClasses",
        config.dataset_name, 
        config.model_name, 
        config.attack_name,
        "res.data"
    ))

    # 得到各个变异率下的target class
    suspicious_class_ans = detect(evalMutationResult,measure_name)
    logging.debug(suspicious_class_ans)
    # 保存实验结果
    save_dir = os.path.join(
        config.exp_root_dir,
        "SuspiciousClasses",
        config.dataset_name, 
        config.model_name, 
        config.attack_name
    )
    os.makedirs(save_dir,exist_ok=True)
    save_file_name = f"SuspiciousClasses_SK_{measure_name}.data"
    save_file_path = os.path.join(save_dir,save_file_name)
    joblib.dump(suspicious_class_ans,save_file_path)
    logging.debug(f"target class结果保存在:{save_file_path}")

def main_v2(measure_name):
    '''
    measure_name: precison | recall | f1-score
    '''
    res = {}
    logging.debug("开始:获得每个变异率下的Suspicious Classes")
    for rate in config.fine_mutation_rate_list:
        logging.debug(f"变异率:{rate}")
        label_df = pd.read_csv(os.path.join(
                config.exp_root_dir,
                "EvalMutationToCSV",
                config.dataset_name,
                config.model_name,
                config.attack_name,
                str(rate),
                "preLabel.csv"))
        # confidence_df = pd.read_csv(os.path.join(
        #         config.exp_root_dir,
        #         "EvalMutationToCSV",
        #         config.dataset_name,
        #         config.model_name,
        #         config.attack_name,
        #         str(rate),
        #         "confidence.csv"))
        
        if measure_name  == "precision":
            class_measure_dict = measure_by_model_precision(label_df)
        elif measure_name == "recall":
            class_measure_dict = measure_by_model_recall(label_df)
        elif measure_name == "f1-score":
            class_measure_dict = measure_by_model_f1_score(label_df)
        elif measure_name == "LCR":
            class_measure_dict = measure_by_model_LCR(label_df)
        elif measure_name == "AccDif":
            class_measure_dict = measure_by_model_AccDif(label_df)
        elif measure_name == "confidence":
            # class_measure_dict = measure_by_model_confidence(confidence_df)
            pass
            
        
        # 把绘制箱线图的数据保存一下
        box_data_save_dir = os.path.join(config.exp_root_dir,"SK",config.dataset_name,config.model_name,config.attack_name)
        save_dir = os.path.join(box_data_save_dir,str(rate))
        os.makedirs(save_dir,exist_ok=True)
        save_file_name = f"box_{measure_name}.csv"
        save_path = os.path.join(save_dir,save_file_name)
        df = pd.DataFrame(class_measure_dict)
        # 直接设置新的列名列表
        new_col_name_list = []
        for old_col_name in list(df.columns):
            new_col_name_list.append("C"+str(old_col_name))
        df.columns = new_col_name_list
        df.to_csv(save_path,index=False)
        suspicious_classes_top,suspicious_classes_low = get_suspicious_classes_by_ScottKnottESD(class_measure_dict)
        res[rate] = {"top":suspicious_classes_top,"low":suspicious_classes_low}
    logging.debug(res)
    # 保存实验结果
    save_dir = os.path.join(
        config.exp_root_dir,
        "SuspiciousClasses",
        config.dataset_name, 
        config.model_name, 
        config.attack_name
    )
    os.makedirs(save_dir,exist_ok=True)
    save_file_name = f"SuspiciousClasses_SK_{measure_name}.data"
    save_file_path = os.path.join(save_dir,save_file_name)
    joblib.dump(res,save_file_path)
    logging.debug(f"SuspiciousClasses结果保存在:{save_file_path}")
    

def measure_by_model_precision(df:pd.DataFrame):
    data_dict = defaultdict(list)
    # ground truth label
    GT_label_list = df["GT_label"]
    for mutated_model_global_id in range(500):
        model_col_name = f"model_{mutated_model_global_id}"
        pred_label_list = list(df[model_col_name])
        report = classification_report(GT_label_list,pred_label_list,output_dict=True, zero_division=0)
        for class_i in range(config.class_num):
            measure = report[str(class_i)]["precision"]
            data_dict[class_i].append(measure)
    return data_dict

def measure_by_model_recall(df:pd.DataFrame):
    data_dict = defaultdict(list)
    # ground truth label
    GT_label_list = df["GT_label"]
    for mutated_model_global_id in range(500):
        model_col_name = f"model_{mutated_model_global_id}"
        pred_label_list = list(df[model_col_name])
        report = classification_report(GT_label_list,pred_label_list,output_dict=True, zero_division=0)
        for class_i in range(config.class_num):
            measure = report[str(class_i)]["recall"]
            data_dict[class_i].append(measure)
    return data_dict

def measure_by_model_f1_score(df:pd.DataFrame):
    data_dict = defaultdict(list)
    # ground truth label
    GT_label_list = df["GT_label"]
    for mutated_model_global_id in range(500):
        model_col_name = f"model_{mutated_model_global_id}"
        pred_label_list = list(df[model_col_name])
        report = classification_report(GT_label_list,pred_label_list,output_dict=True, zero_division=0)
        for class_i in range(config.class_num):
            measure = report[str(class_i)]["f1-score"]
            data_dict[class_i].append(measure)
    return data_dict

def measure_by_model_LCR(df:pd.DataFrame):
    data_dict = defaultdict(list)
    for class_id in range(config.class_num):
        class_df = df.loc[df["GT_label"]==class_id]
        original_model_pred_label_list = list(class_df["original_backdoorModel_preLabel"])
        for mutated_model_global_id in range(500):
            count = 0
            model_col_name = f"model_{mutated_model_global_id}"
            pred_label_list = list(class_df[model_col_name])
            for o_l,m_l in zip(original_model_pred_label_list,pred_label_list):
                if o_l != m_l:
                    count += 1
            lcr = round(count/len(original_model_pred_label_list),4)
            data_dict[class_id].append(lcr)
    return data_dict

def measure_by_model_AccDif(df:pd.DataFrame):
    '''
    注意：在单类别上acc === recall且precision === 1
    '''
    data_dict = defaultdict(list)
    # ground truth label
    GT_label_list = df["GT_label"]
    original_backdoorModel_preLabel_list = df["original_backdoorModel_preLabel"]
    report_o = classification_report(GT_label_list,original_backdoorModel_preLabel_list,output_dict=True, zero_division=0)
    for mutated_model_global_id in range(500):
        model_col_name = f"model_{mutated_model_global_id}"
        pred_label_list = list(df[model_col_name])
        report_m = classification_report(GT_label_list,pred_label_list,output_dict=True, zero_division=0)
        for class_i in range(config.class_num):
            measure = abs(report_m[str(class_i)]["recall"]-report_o[str(class_i)]["recall"])
            data_dict[class_i].append(measure)
    return data_dict

def measure_by_model_confidence(df:pd.DataFrame):
    data_dict = defaultdict(list)
    for class_id in range(config.class_num):
        class_df = df.loc[df["GT_label"]==class_id]
        for mutated_model_global_id in range(500):
            confidence_list = []
            model_col_name = f"model_{mutated_model_global_id}"
            confidence_list = list(class_df[model_col_name])
            avg_confidence = round(sum(confidence_list) / len(confidence_list),4)
            data_dict[class_id].append(avg_confidence)
    return data_dict

    

def measure_by_sample_entropy(df:pd.DataFrame):
    data_dict = defaultdict(list)
    for class_id in range(config.class_num):
        class_df = df.loc[df["GT_label"]==class_id]
        for row_id, row in class_df.iterrows():
            # 当前row(样本)在所有变异模型集上的预测标签
            cur_row_labels = []
            for mutated_model_global_id in range(500):
                model_col_name = f"model_{mutated_model_global_id}"
                cur_row_labels.append(row[model_col_name])
            # 计算该row(样本)的标签熵
            cur_entropy = entropy(cur_row_labels)
            data_dict[class_id].append(cur_entropy)
    return data_dict



def see_res(measure_name):
    data_dir = os.path.join(
        config.exp_root_dir,
        "SuspiciousClasses",
        config.dataset_name, 
        config.model_name, 
        config.attack_name
    )
    file_name = f"SuspiciousClasses_SK_{measure_name}.data"
    data_path = os.path.join(data_dir,file_name)
    data_dict = joblib.load(data_path)
    target_class_idx = config.target_class_idx
    for rate in config.fine_mutation_rate_list:
        print(f"rate:{rate}")
        top_class_list = data_dict[rate]["top"]
        low_class_list = data_dict[rate]["low"]
        if target_class_idx in top_class_list:
            print("Top_group")
            ranking_within_the_group = top_class_list.index(target_class_idx)
            print(f"ranking_within_the_group:{ranking_within_the_group}")
        if target_class_idx in low_class_list:
            print("Low_group")
            ranking_within_the_group = low_class_list.index(target_class_idx)
            print(f"ranking_within_the_group:{ranking_within_the_group}")
        if (target_class_idx not in top_class_list) and (target_class_idx not in low_class_list):
            print("Mid_group")
        
            
if __name__ == "__main__":
    # 进程名称
    measure_name = "AccDif" # precision|recall|f1-score|LCR|AccDif|confidence
    proctitle = f"SuspiciousClasses_SK_{measure_name}|{config.dataset_name}|{config.model_name}|{config.attack_name}"
    setproctitle.setproctitle(proctitle)
    device = torch.device(f"cuda:{config.gpu_id}")

    # 日志保存目录
    LOG_FORMAT = "时间：%(asctime)s - 日志等级：%(levelname)s - 日志信息：%(message)s"
    LOG_FILE_DIR = os.path.join("log",config.dataset_name,config.model_name,config.attack_name)
    os.makedirs(LOG_FILE_DIR,exist_ok=True)
    LOG_FILE_NAME = f"SuspiciousClasses_SK_{measure_name}.log"
    LOG_FILE_PATH = os.path.join(LOG_FILE_DIR,LOG_FILE_NAME)
    logging.basicConfig(level=logging.DEBUG,format=LOG_FORMAT,filename=LOG_FILE_PATH,filemode="w")
    logging.debug(proctitle)

    try:
        # main_v1(measure_name)
        # main_v2(measure_name)
        see_res(measure_name)
        pass
    except Exception as e:
        logging.error("发生异常:%s",e)


    