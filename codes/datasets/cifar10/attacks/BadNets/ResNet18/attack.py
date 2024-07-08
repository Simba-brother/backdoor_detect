import os
import time
import cv2
import numpy as np
import random
import setproctitle
import torch
import torch.nn as nn
from torchvision.datasets import DatasetFolder
from torch.utils.data import DataLoader
from torchvision.transforms import Compose, ToTensor, RandomHorizontalFlip, ToPILImage, RandomCrop

from codes.core.attacks import BadNets
from codes.core.models.resnet import ResNet
from codes.scripts.dataset_constructor import ExtractDataset, PureCleanTrainDataset, PurePoisonedTrainDataset
from codes import config

global_seed = 666
deterministic = True
# cpu种子
torch.manual_seed(global_seed)

def _seed_worker(worker_id):
    np.random.seed(global_seed)
    random.seed(global_seed)

# 训练集transform    
transform_train = Compose([
    # Convert a tensor or an ndarray to PIL Image
    ToPILImage(), 
    # img (PIL Image or Tensor): Image to be cropped.
    RandomCrop(size=32,padding=4,padding_mode="reflect"), 
    RandomHorizontalFlip(), 
    # Converts a PIL Image or numpy.ndarray (H x W x C) in the range [0, 255] to a torch.FloatTensor of shape (C x H x W) in the range [0.0, 1.0]
    ToTensor()
])
# 测试集transform
transform_test = Compose([
    ToPILImage(),
    ToTensor()
])
# victim model
model = ResNet(num=18,num_classes=10)
# 获得数据集
trainset = DatasetFolder(
    root= os.path.join(config.CIFAR10_dataset_dir, "train"),
    loader=cv2.imread, # ndarray (H,W,C)
    extensions=('png',),
    transform=transform_train,
    target_transform=None,
    is_valid_file=None)

testset = DatasetFolder(
    root= os.path.join(config.CIFAR10_dataset_dir, "test"),
    loader=cv2.imread,
    extensions=('png',),
    transform=transform_test,
    target_transform=None,
    is_valid_file=None)

# backdoor pattern
pattern = torch.zeros((32, 32), dtype=torch.uint8)
pattern[-3:, -3:] = 255
weight = torch.zeros((32, 32), dtype=torch.float32)
weight[-3:, -3:] = 1.0


badnets = BadNets(
    train_dataset=trainset,
    test_dataset=testset,
    model=model,
    loss=nn.CrossEntropyLoss(),
    y_target=config.target_class_idx,
    poisoned_rate=config.poisoned_rate,
    pattern=pattern,
    weight=weight,
    seed=global_seed,
    deterministic=deterministic
)

    
# Train Attacked Model (schedule is the same as https://github.com/THUYimingLi/Open-sourced_Dataset_Protection/blob/main/CIFAR/train_watermarked.py)
exp_root_dir = "/data/mml/backdoor_detect/experiments"
dataset_name = "CIFAR10"
model_name = "ResNet18"
attack_name = "BadNets"
schedule = {
    'device': 'cuda:0',
    
    'benign_training': False,
    'batch_size': 128,
    'num_workers': 4,

    'lr': 0.1,
    'momentum': 0.9,
    'weight_decay': 5e-4,
    'gamma': 0.1,
    'schedule': [150, 180], # epoch区间

    'epochs': 200,

    'log_iteration_interval': 100,
    'test_epoch_interval': 10,
    'save_epoch_interval': 10,

    'save_dir': os.path.join(exp_root_dir, "attack", dataset_name, model_name, attack_name),
    'experiment_name': 'attack'
}


def attack():
    # 攻击
    badnets.train(schedule)
    # 工作dir
    work_dir = badnets.work_dir
    # 获得backdoor model weights
    backdoor_model = badnets.best_model
    # clean testset
    clean_testset = testset
    # poisoned testset
    poisoned_testset = badnets.poisoned_test_dataset
    # poisoned trainset
    poisoned_trainset = badnets.poisoned_train_dataset
    # poisoned_ids
    poisoned_ids = poisoned_trainset.poisoned_set
    # pure clean trainset
    pureCleanTrainDataset = PureCleanTrainDataset(poisoned_trainset, poisoned_ids)
    # pure poisoned trainset
    purePoisonedTrainDataset = PurePoisonedTrainDataset(poisoned_trainset, poisoned_ids)

    dict_state = {}
    dict_state["backdoor_model"] = backdoor_model
    dict_state["poisoned_trainset"]=poisoned_trainset
    dict_state["poisoned_ids"]=poisoned_ids
    dict_state["pureCleanTrainDataset"] = pureCleanTrainDataset
    dict_state["purePoisonedTrainDataset"] = purePoisonedTrainDataset
    dict_state["clean_testset"]=clean_testset
    dict_state["poisoned_testset"]=poisoned_testset
    dict_state["pattern"] = pattern
    dict_state['weight']=weight
    save_file_name = f"dict_state.pth"
    save_path = os.path.join(work_dir, save_file_name)
    torch.save(dict_state, save_path)
    print(f"BadNets攻击完成,数据和日志被存入{save_path}")

def eval(model,testset):
    model.eval()
    device = torch.device("cuda:1")
    model.to(device)
    batch_size = 128
    # 加载trigger set
    testset_loader = DataLoader(
        testset,
        batch_size = batch_size,
        shuffle=False,
        # num_workers=self.current_schedule['num_workers'],
        drop_last=False,
        pin_memory=False,
        worker_init_fn=_seed_worker
    )
    # 测试集总数
    total_num = len(testset_loader.dataset)
    # 评估开始时间
    start = time.time()
    acc = torch.tensor(0., device=device)
    correct_num = 0 # 攻击成功数量
    with torch.no_grad():
        for batch_id, batch in enumerate(testset_loader):
            X = batch[0]
            Y = batch[1]
            X = X.to(device)
            Y = Y.to(device)
            pridict_digits = model(X)
            correct_num += (torch.argmax(pridict_digits, dim=1) == Y).sum()
        acc = correct_num / total_num
        acc = round(acc.item(),3)
    end = time.time()
    print("acc:",acc)
    print(f'Total eval() time: {end-start:.1f} seconds')
    return acc


def get_dict_state():
    dict_state_file_path = os.path.join(exp_root_dir,"attack",dataset_name,model_name, attack_name, "attack", "dict_state.pth")
    dict_state = torch.load(dict_state_file_path,map_location="cpu")
    return dict_state

def update_dict_state():
    # dict_state_file_path = os.path.join(exp_root_dir,"attack",dataset_name,model_name, attack_name, "attack", "dict_state.pth")
    # dict_state_file_path = os.path.join(exp_root_dir,"attack",dataset_name,model_name, attack_name, "attack_2024-06-19_20:18:49", "dict_state.pth")
    dict_state_file_path = "/data/mml/backdoor_detect/experiments/attack/CIFAR10/ResNet18/BadNets/attack_2024-06-22_16:15:38/dict_state.pth"
    dict_state = torch.load(dict_state_file_path,map_location="cpu")
    dict_state["poisoned_trainset"] = ExtractDataset(dict_state["poisoned_trainset"]) 
    dict_state["poisoned_testset"] = ExtractDataset(dict_state["poisoned_testset"]) 

    # clean_testset = dict_state["clean_testset"]
    # clean_testset = ExtractDataset(dict_state["clean_testset"]) 
    # dict_state["clean_testset"] = clean_testset
    # dict_state_file_path = os.path.join(exp_root_dir,"attack",dataset_name,model_name, attack_name, "attack_2024-06-19_20:18:49", "dict_state_new.pth")

    torch.save(dict_state, "/data/mml/backdoor_detect/experiments/attack/CIFAR10/ResNet18/BadNets/attack_2024-06-22_16:15:38/new_dict_state.pth")
    print("update_dict_state() success")

def create_backdoor_data():
    # creat
    dict_state_file_path = os.path.join(exp_root_dir,"attack",dataset_name,model_name, attack_name, "attack_2024-06-22_16:15:38", "dict_state.pth")
    dict_state = torch.load(dict_state_file_path,map_location="cpu")
    backdoor_model = dict_state["backdoor_model"]
    poisoned_trainset =  badnets.poisoned_train_dataset
    poisoned_testset =  badnets.poisoned_test_dataset
    clean_testset = testset
    # eval
    poisoned_trainset_acc = eval(backdoor_model, poisoned_trainset)
    poisoned_testset_acc = eval(backdoor_model, poisoned_testset)
    clean_testset_acc = eval(backdoor_model,testset)
    print("poisoned_trainset_acc",poisoned_trainset_acc)
    print("poisoned_testset_acc", poisoned_testset_acc)
    print("clean_testset_acc", clean_testset_acc)
    # save
    save_dir = os.path.join(exp_root_dir, "attack", dataset_name, model_name, attack_name)
    save_file_name = "backdoor_data.pth"
    backdoor_data = {
        "backdoor_model":backdoor_model,
        "poisoned_trainset":poisoned_trainset,
        "poisoned_testset":poisoned_testset,
        "clean_testset":testset,
        "poisoned_ids":poisoned_trainset.poisoned_set
    }
    save_file_path = os.path.join(save_dir,save_file_name)
    torch.save(backdoor_data,save_file_path)
    print(f"backdoor_data is saved in {save_file_path}")

def eval_backdoor():
    backdoor_data_path = os.path.join(exp_root_dir, "attack", dataset_name, model_name, attack_name, "backdoor_data.pth")
    backdoor_data = torch.load(backdoor_data_path, map_location="cpu")
    backdoor_model = backdoor_data["backdoor_model"]
    poisoned_trainset = backdoor_data["poisoned_trainset"]
    poisoned_testset = backdoor_data["poisoned_testset"]
    clean_testset = backdoor_data["clean_testset"]
    poisoned_ids = backdoor_data["poisoned_ids"]
    # eval
    poisoned_trainset_acc = eval(backdoor_model, poisoned_trainset)
    poisoned_testset_acc = eval(backdoor_model, poisoned_testset)
    clean_testset_acc = eval(backdoor_model,clean_testset)
    print("poisoned_trainset_acc",poisoned_trainset_acc)
    print("poisoned_testset_acc", poisoned_testset_acc)
    print("clean_testset_acc", clean_testset_acc)

if __name__ == "__main__":
    proc_title = "EvalBackdoor|"+dataset_name+"|"+model_name+"|"+attack_name
    setproctitle.setproctitle(proc_title)
    print(proc_title)
    # attack()
    # create_backdoor_data()
    eval_backdoor()
    pass