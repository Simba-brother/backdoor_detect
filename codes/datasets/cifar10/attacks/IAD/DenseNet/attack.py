import os
import random

import numpy as np
import cv2
import torch
import torch.nn as nn

from torchvision.transforms import Compose, ToTensor, RandomHorizontalFlip, ToPILImage, Resize, RandomCrop, RandomRotation, Normalize
from torch.utils.data import DataLoader
from torchvision.datasets import DatasetFolder

from codes.core.attacks import IAD
from codes.datasets.cifar10.models.densenet import densenet_cifar
import setproctitle
from codes.scripts.dataset_constructor import *
from codes.core.attacks.IAD import Generator
from codes import config
from codes.datasets.eval_backdoor import eval_backdoor

# 设置随机种子
global_seed = config.random_seed
deterministic = True
torch.manual_seed(global_seed)

def _seed_worker(worker_id):
    worker_seed =666
    np.random.seed(worker_seed)
    random.seed(worker_seed)

# 获得一个朴素的resnet18
model = densenet_cifar()

# 使用BackdoorBox的transform
transform_train = Compose([
    ToPILImage(),
    Resize((32, 32)),
    RandomCrop((32, 32), padding=5),
    RandomRotation(10),
    RandomHorizontalFlip(p=0.5),
    ToTensor(),
    Normalize((0.4914, 0.4822, 0.4465),
                        (0.247, 0.243, 0.261))
])
transform_test = Compose([
    ToPILImage(),
    Resize((32, 32)),
    ToTensor(),
    Normalize((0.4914, 0.4822, 0.4465),(0.247, 0.243, 0.261)) # imageNet
])
# 获得数据集
trainset = DatasetFolder(
    root='/data/mml/backdoor_detect/dataset/cifar10/train',
    loader=cv2.imread, # ndarray
    extensions=('png',),
    transform=transform_train,
    target_transform=None,
    is_valid_file=None)
# 另外一份训练集
trainset1 = DatasetFolder(
    root='/data/mml/backdoor_detect/dataset/cifar10/train',
    loader=cv2.imread, # ndarray
    extensions=('png',),
    transform=transform_train,
    target_transform=None,
    is_valid_file=None)
testset = DatasetFolder(
    root='/data/mml/backdoor_detect/dataset/cifar10/test',
    loader=cv2.imread,
    extensions=('png',),
    transform=transform_test,
    target_transform=None,
    is_valid_file=None)
# 另外一份测试集
testset1 = DatasetFolder(
    root='/data/mml/backdoor_detect/dataset/cifar10/test',
    loader=cv2.imread,
    extensions=('png',),
    transform=transform_test,
    target_transform=None,
    is_valid_file=None)
# 获得加载器
batch_size = 128
trainset_loader = DataLoader(
    trainset,
    batch_size = batch_size,
    shuffle=True,
    # num_workers=self.current_schedule['num_workers'],
    drop_last=False,
    pin_memory=False,
    worker_init_fn=_seed_worker
    )
testset_loader = DataLoader(
    testset,
    batch_size = batch_size,
    shuffle=False,
    # num_workers=self.current_schedule['num_workers'],
    drop_last=False,
    pin_memory=False,
    worker_init_fn=_seed_worker
    )

exp_root_dir = config.exp_root_dir
dataset_name = "CIFAR10"
model_name = "DenseNet"
attack_name = "IAD"
schedule = {
    'device': 'cuda:1',
    'GPU_num': 1,

    'benign_training': False,
    'batch_size': 128,
    'num_workers': 4,

    'lr': 0.01,
    'momentum': 0.9,
    'weight_decay': 5e-4,
    'milestones': [100, 200, 300, 400],
    'lambda': 0.1,
    
    'lr_G': 0.01,
    'betas_G': (0.5, 0.9),
    'milestones_G': [200, 300, 400, 500],
    'lambda_G': 0.1,

    'lr_M': 0.01,
    'betas_M': (0.5, 0.9),
    'milestones_M': [10, 20],
    'lambda_M': 0.1,
    
    'epochs': 600,
    'epochs_M': 25,

    'log_iteration_interval': 100,
    'test_epoch_interval': 10,
    'save_epoch_interval': 10,

    'save_dir': os.path.join(exp_root_dir, "ATTACK", dataset_name, model_name, attack_name),
    'experiment_name': 'ATTACK'
}

iad = IAD(
    dataset_name="cifar10",
    train_dataset=trainset,
    test_dataset=testset,
    train_dataset1=trainset1,
    test_dataset1=testset1,
    model=model,
    loss=nn.CrossEntropyLoss(),
    y_target=config.target_class_idx,
    poisoned_rate=config.poisoned_rate,
    cross_rate=config.poisoned_rate,
    lambda_div=1,
    lambda_norm=100,
    mask_density=0.032,
    EPSILON=1e-7,
    schedule=schedule,
    seed=global_seed,
    deterministic=deterministic
)



def attack():
    iad.train()
    return os.path.join(iad.work_dir,"dict_state.pth")

def create_backdoor_data(attack_dict_path):
    # create
    dict_state_file_path = os.path.join(attack_dict_path)
    dict_state = torch.load(dict_state_file_path, map_location="cpu")
    model.load_state_dict(dict_state["model"])
    backdoor_model = model

    modelG = Generator("cifar10")
    modelM = Generator("cifar10", out_channels=1)
    
    modelG.load_state_dict(dict_state["modelG"])
    modelM.load_state_dict(dict_state["modelM"])

    backdoor_model.eval()
    modelG.eval()
    modelM.eval()

    poisoned_trainset =  IADPoisonedDatasetFolder(
        benign_dataset = trainset,
        y_target = config.target_class_idx,
        poisoned_rate = config.poisoned_rate,
        modelG = modelG,
        modelM =modelM
    )

    poisoned_ids = poisoned_trainset.poisoned_set

    poisoned_testset =  IADPoisonedDatasetFolder(
        benign_dataset = testset,
        y_target = config.target_class_idx,
        poisoned_rate = 1,
        modelG = modelG,
        modelM = modelM
    )
    
    # 将数据集抽取到内存，为了加速评估
    poisoned_trainset = ExtractDataset(poisoned_trainset)
    poisoned_testset = ExtractDataset(poisoned_testset)

    # save
    save_dir = os.path.join(exp_root_dir, "ATTACK", dataset_name, model_name, attack_name)
    save_file_name = "backdoor_data.pth"
    backdoor_data = {
        "backdoor_model":backdoor_model,
        "poisoned_trainset":poisoned_trainset,
        "poisoned_testset":poisoned_testset,
        "clean_testset":testset,
        "poisoned_ids":poisoned_ids
    }
    save_file_path = os.path.join(save_dir,save_file_name)
    torch.save(backdoor_data,save_file_path)
    print(f"backdoor_data is saved in {save_file_path}")


def update_backdoor_data():
    backdoor_data_path = os.path.join(exp_root_dir, "ATTACK", dataset_name, model_name, attack_name, "backdoor_data.pth")
    backdoor_data = torch.load(backdoor_data_path, map_location="cpu")
    backdoor_model = backdoor_data["backdoor_model"]
    poisoned_trainset = backdoor_data["poisoned_trainset"]
    poisoned_testset = backdoor_data["poisoned_testset"]
    clean_testset = backdoor_data["clean_testset"]
    poisoned_ids = backdoor_data["poisoned_ids"]

    # 将数据集抽取到内存，为了加速评估
    poisoned_trainset = ExtractDataset(poisoned_trainset)
    poisoned_testset = ExtractDataset(poisoned_testset)
    backdoor_data["poisoned_trainset"] = poisoned_trainset
    backdoor_data["poisoned_testset"] = poisoned_testset

    # 保存数据
    torch.save(backdoor_data, backdoor_data_path)
    print("update_backdoor_data(),successful.")

def main():
    proc_title = "ATTACK|"+dataset_name+"|"+attack_name+"|"+model_name
    setproctitle.setproctitle(proc_title)
    print(proc_title)
    # 开始攻击并保存攻击模型和数据
    attack_dict_path = attack()
    # 抽取攻击模型和数据并转储
    backdoor_data_path = create_backdoor_data(attack_dict_path)
    # 开始评估
    eval_backdoor(dataset_name,attack_name,model_name)

if __name__ == "__main__":
    # main()
    # update_backdoor_data()
    # eval_backdoor(dataset_name,attack_name,model_name)
    pass
