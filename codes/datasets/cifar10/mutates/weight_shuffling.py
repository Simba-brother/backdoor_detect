import sys
sys.path.append("./")
import time
import os
import copy
import math
import random
import numpy as np
import torch.nn as nn
import torch
from torch.utils.data import DataLoader,Dataset
from codes import utils



attack_method = "IAD" # BadNets, Blended, IAD, LabelConsistent, Refool, WaNet
device = torch.device('cuda:6')
if attack_method == "BadNets":
    from codes.datasets.cifar10.attacks.badnets_resnet18_nopretrain_32_32_3 import PureCleanTrainDataset, PurePoisonedTrainDataset, get_dict_state
elif attack_method == "Blended":
    from codes.datasets.cifar10.attacks.Blended_resnet18_nopretrain_32_32_3 import PureCleanTrainDataset, PurePoisonedTrainDataset, get_dict_state
elif attack_method == "IAD":
    from codes.datasets.cifar10.attacks.IAD_resnet18_nopretrain_32_32_3 import PoisonedTrainDataset, PurePoisonedTrainDataset, PureCleanTrainDataset, PoisonedTestSet, TargetClassCleanTrainDataset,  get_dict_state
elif attack_method == "LabelConsistent":
    from codes.datasets.cifar10.attacks.LabelConsistent_resnet18_nopretrain_32_32_3 import PureCleanTrainDataset, PurePoisonedTrainDataset, get_dict_state
elif attack_method == "Refool":
    from codes.datasets.cifar10.attacks.Refool_resnet18_nopretrain_32_32_3 import PureCleanTrainDataset, PurePoisonedTrainDataset, get_dict_state
elif attack_method == "WaNet":
    from codes.datasets.cifar10.attacks.WaNet_resnet18_nopretrain_32_32_3 import PureCleanTrainDataset, PurePoisonedTrainDataset, get_dict_state


global_seed = 666
deterministic = True
torch.manual_seed(global_seed)
np.random.seed(global_seed)
random.seed(global_seed)

origin_dict_state = get_dict_state()
# 本脚本全局变量
# 待变异的后门模型
backdoor_model = origin_dict_state["backdoor_model"]
clean_testset = origin_dict_state["clean_testset"]
poisoned_testset = origin_dict_state["poisoned_testset"]
pureCleanTrainDataset = origin_dict_state["pureCleanTrainDataset"]
purePoisonedTrainDataset = origin_dict_state["purePoisonedTrainDataset"]
clean_testset = origin_dict_state["clean_testset"]
poisoned_testset = origin_dict_state["poisoned_testset"]
# mutated model 保存目录
mutate_ratio = 0.2
mutation_num = 50
work_dir = f"/data/mml/backdoor_detect/experiments/CIFAR10/resnet18_nopretrain_32_32_3/mutates/weight_shuffle/ratio_{mutate_ratio}_num_{mutation_num}/{attack_method}"
# 保存变异模型权重
save_dir = work_dir
utils.create_dir(save_dir)


def _seed_worker():
    worker_seed =  666 # torch.initial_seed() % 2**32
    np.random.seed(worker_seed)
    random.seed(worker_seed)

def shuffle_conv2d_weights(weight, neuron_id):
    o_c, in_c, h, w =  weight.shape
    weight = weight.reshape(o_c, in_c * h * w)
    row = weight[neuron_id]
    idx = torch.randperm(row.nelement())
    row = row.view(-1)[idx].view(row.size())
    row.requires_grad_()
    weight[neuron_id] = row
    weight = weight.reshape(o_c, in_c, h, w)
    return weight

def mutate(model, mutate_ratio):
    for count in range(mutation_num):
        model_copy = copy.deepcopy(model)
        layers = [module for module in model_copy.modules()]
        # 遍历各层
        with torch.no_grad():
            for layer in layers[1:]:
                # if isinstance(layer, nn.Conv2d):
                #     weight = layer.weight # weight shape:our_channels, in_channels, kernel_size_0,kernel_size_1
                #     out_channels = layer.out_channels
                #     cur_layer_neuron_num = out_channels
                #     mutate_num = math.ceil(cur_layer_neuron_num*mutate_ratio)
                #     cur_layer_neuron_ids = list(range(cur_layer_neuron_num))
                #     selected_cur_layer_neuron_ids = random.sample(cur_layer_neuron_ids,mutate_num)
                #     for neuron_id in selected_cur_layer_neuron_ids:
                #         shuffle_conv2d_weights(weight, neuron_id)
                if isinstance(layer, nn.Linear):
                    weight = layer.weight # weight shape:output, input
                    out_features, in_features = weight.shape
                    # cur_layer_neuron_num = out_features
                    last_layer_neuron_num = in_features
                    mutate_num = math.ceil(last_layer_neuron_num*mutate_ratio)
                    last_layer_neuron_ids = list(range(last_layer_neuron_num))
                    selected_last_layer_neuron_ids = random.sample(last_layer_neuron_ids,mutate_num)
                    for neuron_id in selected_last_layer_neuron_ids:
                        col = weight[:,neuron_id]
                        idx = torch.randperm(col.nelement())
                        col = col.view(-1)[idx].view(col.size())
                        col.requires_grad_()
                        weight[:,neuron_id] = col
        file_name = f"model_mutated_{count+1}.pth"
        save_path = os.path.join(save_dir, file_name)
        torch.save(model_copy.state_dict(), save_path)
        print(f"变异模型:{file_name}保存成功, 保存位置:{save_path}")
    print("mutate() success")

def eval(m_i, testset):
    # 得到模型结构
    model = backdoor_model
    # 加载backdoor weights
    state_dict = torch.load(os.path.join(work_dir, f"model_mutated_{m_i}.pth"), map_location="cpu")
    model.load_state_dict(state_dict)
    total_num = len(testset)
    batch_size =128
    testset_loader = DataLoader(
        testset,
        batch_size = batch_size,
        shuffle=False,
        # num_workers=self.current_schedule['num_workers'],
        drop_last=False,
        pin_memory=False,
        worker_init_fn=_seed_worker
    )
    # 评估开始时间
    start = time.time()
    model.to(device)
    model.eval()  # put network in train mode for Dropout and Batch Normalization
    acc = torch.tensor(0., device=device) # 攻击成功率
    correct_num = 0 # 攻击成功数量
    with torch.no_grad():
        for X, Y in testset_loader:
            X = X.to(device)
            Y = Y.to(device)
            preds = model(X)
            correct_num += (torch.argmax(preds, dim=1) == Y).sum()
    acc = correct_num/total_num
    acc = round(acc.item(),3)
    end = time.time()
    return acc

if __name__ == "__main__":
    mutate(backdoor_model, mutate_ratio)
    pure_clean_trainset_acc_list = []
    pure_poisoned_trainset_asr_list = []
    clean_testset_acc_list = []
    poisoned_testset_asr_list = []
    asr_list = []
    for m_i in range(mutation_num):
        pure_clean_trainset_acc = eval(m_i+1, pureCleanTrainDataset)
        pure_poisoned_trainset_asr = eval(m_i+1, purePoisonedTrainDataset)
        clean_testset_acc = eval(m_i+1, clean_testset)
        poisoned_testset_asr = eval(m_i+1, poisoned_testset)
        print(f"pure_clean_trainset_acc:{pure_clean_trainset_acc}, pure_poisoned_trainset_asr:{pure_poisoned_trainset_asr}")
        print(f"clean_testset_acc:{clean_testset_acc}, poisoned_testset_asr:{poisoned_testset_asr}")
        pure_clean_trainset_acc_list.append(pure_clean_trainset_acc)
        pure_poisoned_trainset_asr_list.append(pure_poisoned_trainset_asr)
        clean_testset_acc_list.append(clean_testset_acc)
        poisoned_testset_asr_list.append(poisoned_testset_asr)
    print(pure_clean_trainset_acc_list,"\n")
    print(f"pure_clean_trainset_acc_list mean:{np.mean(pure_clean_trainset_acc_list)}","\n")
    print(pure_poisoned_trainset_asr_list,"\n")
    print(f"pure_poisoned_trainset_asr_list mean:{np.mean(pure_poisoned_trainset_asr_list)}", "\n")
    print(clean_testset_acc_list,"\n")
    print(f"clean_testset_acc_list mean:{np.mean(clean_testset_acc_list)}","\n")
    print(poisoned_testset_asr_list,"\n")
    print(f"poisoned_testset_asr_list mean:{np.mean(poisoned_testset_asr_list)}", "\n")
    pass
