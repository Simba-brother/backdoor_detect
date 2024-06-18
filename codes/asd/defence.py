# ASD baseline mian file

import joblib
import setproctitle
import os
from copy import deepcopy
import cv2
import numpy as np
import torch
import torch.nn as nn
# import torch.nn.functional as F
from torch.utils.data import DataLoader # 用于批量加载训练集的

from torchvision.transforms import Compose, ToTensor, RandomHorizontalFlip, ToPILImage, Resize, RandomCrop
from torchvision.datasets import DatasetFolder
from codes import config
from codes.core.attacks import BadNets
from codes.core.models.resnet import ResNet
from codes.asd.loss import SCELoss, MixMatchLoss
from codes.asd.semi import poison_linear_record, mixmatch_train,linear_test
from codes.asd.dataset import MixMatchDataset
# from ASD.log import result2csv
from codes.utils import create_dir
# from ASD.models.resnet_cifar import get_model

def main_test():
    # 进程名称
    proctitle = f"ASD|{config.dataset_name}|{config.model_name}|{config.attack_name}"
    setproctitle.setproctitle(proctitle)
    print(f"proctitle:{proctitle}")
    # 准备数据集
    dataset = prepare_data()
    trainset = dataset["trainset"]
    testset = dataset["testset"]
    # 准备victim model
    model = prepare_model()
    # 攻击
    backdoor_data = backdoor_attack(trainset,testset,model)
    # ASD防御训练
    defence_train(
        model = backdoor_data["victim_model"],
        class_num = config.class_num,
        poisoned_train_dataset = backdoor_data["poisoned_train_dataset"], # 有污染的训练集
        poisoned_ids = backdoor_data["poisoned_ids"], # 被污染的样本id list
        poisoned_eval_dataset_loader = backdoor_data["poisoned_eval_dataset_loader"], # 有污染的验证集加载器（可以是有污染的训练集不打乱加载）
        poisoned_train_dataset_loader = backdoor_data["poisoned_train_dataset_loader"], #有污染的训练集加载器
        clean_test_dataset_loader = backdoor_data["clean_test_dataset_loader"], # 干净的测试集加载器
        poisoned_test_dataset_loader = backdoor_data["poisoned_test_dataset_loader"], # 污染的测试集加载器
        device = torch.device(f"cuda:{config.gpu_id}"),
        save_dir = os.path.join(config.exp_root_dir, "ASD", config.dataset_name, config.model_name, config.attack_name)
    )

def backdoor_attack(trainset, testset, model, random_seed=666, deterministic=True):
    '''
    攻击方法：
    Args:
        trainset: 训练集(已经经过了普通的transforms)
        trainset: 测试集(已经经过了普通的transforms)
        model: victim model
    Return:
        攻击后的字典数据
    '''
    if config.attack_name == "BadNets":
        pattern = torch.zeros((32, 32), dtype=torch.uint8)
        pattern[-3:, -3:] = 255
        weight = torch.zeros((32, 32), dtype=torch.float32)
        weight[-3:, -3:] = 1.0
              
        # torch.manual_seed(global_seed)
        # np.random.seed(global_seed)
        # random.seed(global_seed)
        badnets = BadNets(
            train_dataset=trainset,
            test_dataset=testset,
            model=model,
            loss=nn.CrossEntropyLoss(),
            y_target=config.target_class_idx,
            poisoned_rate=0.1,
            pattern=pattern,
            weight=weight,
            seed=random_seed,
            poisoned_transform_train_index= -1,
            poisoned_transform_test_index= -1,
            poisoned_target_transform_index=0,
            deterministic=deterministic
        )
        # 被污染的训练集
        poisoned_train_dataset = badnets.poisoned_train_dataset
        # 数据集中被污染的实例id
        poisoned_ids = poisoned_train_dataset.poisoned_set
        # 被污染的训练集加载器
        poisoned_train_dataset_loader = DataLoader(
            poisoned_train_dataset,
            batch_size=64,
            shuffle=True,
            num_workers=4,
            pin_memory=True,
            )
        # 验证集加载器（可以是被污染的训练集但是不打乱）
        poisoned_eval_dataset_loader = DataLoader(
            poisoned_train_dataset,
            batch_size=64,
            shuffle=False,
            num_workers=4,
            pin_memory=True,
            )
        # 被污染的测试集
        poisoned_test_dataset = badnets.poisoned_test_dataset
        # 被污染的测试集加载器
        poisoned_test_dataset_loader = DataLoader(
            poisoned_test_dataset,
            batch_size=64,
            shuffle=False,
            num_workers=4,
            pin_memory=True,
            )
        # 干净污染的测试集加载器
        clean_test_dataset_loader = DataLoader(
            testset,
            batch_size=64,
            shuffle=False,
            num_workers=4,
            pin_memory=True,
            )
    res = {
        "victim_model":model,
        "poisoned_train_dataset":poisoned_train_dataset,
        "poisoned_ids":poisoned_ids,
        "poisoned_train_dataset_loader":poisoned_train_dataset_loader,
        "poisoned_eval_dataset_loader":poisoned_eval_dataset_loader,
        "poisoned_test_dataset":poisoned_test_dataset,
        "poisoned_test_dataset_loader":poisoned_test_dataset_loader,
        "clean_testset":testset,
        "clean_trainset":trainset,
        "clean_test_dataset_loader":clean_test_dataset_loader,
    }
    return res
    
def prepare_model():
    '''
    准备模型
    Return:
        model
    '''
    if config.model_name == "ResNet18":
        model = ResNet(num=18, num_classes=config.class_num)
    return model

def prepare_data():
    '''
    准备训练集和测试集
    '''
    # 加载数据集
    # 训练集transform    
    transform_train = Compose([
        # Convert a tensor or an ndarray to PIL Image
        ToPILImage(), 
        # 训练数据增强,随机水平翻转 
        RandomCrop(size=32,padding=4,padding_mode="reflect"), # img (PIL Image or Tensor): Image to be cropped.
        RandomHorizontalFlip(),
        ToTensor() # Converts a PIL Image or numpy.ndarray (H x W x C) in the range [0, 255] to a torch.FloatTensor of shape (C x H x W) in the range [0.0, 1.0]
    ])
    # 测试集transform
    transform_test = Compose([
        ToPILImage(),
        ToTensor()
    ])
    # 数据集文件夹
    dataset_dir = config.CIFAR10_dataset_dir
    # 获得训练数据集
    trainset = DatasetFolder(
        root=os.path.join(dataset_dir, "train"),
        loader=cv2.imread, # ndarray (H,W,C)
        extensions=('png',),
        transform=transform_train,
        target_transform=None,
        is_valid_file=None)
    # 获得测试数据集
    testset = DatasetFolder(
        root=os.path.join(dataset_dir, "test"),
        loader=cv2.imread, # ndarray (H,W,C)
        extensions=('png',),
        transform=transform_test,
        target_transform=None,
        is_valid_file=None)
    res = {
        "trainset":trainset,
        "testset":testset,
    }
    return res
    
def defence_train(
        model, # victim model
        class_num, # 分类数量
        poisoned_train_dataset, # 有污染的训练集
        poisoned_ids, # 被污染的样本id list
        poisoned_eval_dataset_loader, # 有污染的验证集加载器（可以是有污染的训练集不打乱加载）
        poisoned_train_dataset_loader, #有污染的训练集加载器
        clean_test_dataset_loader, # 干净的测试集加载器
        poisoned_test_dataset_loader, # 污染的测试集加载器
        device, # GPU设备对象
        save_dir # 实验结果存储目录 save_dir = os.path.join(exp_root_dir, "ASD", dataset_name, model_name, attack_name)
        ):
    '''
    ASD防御训练方法
    '''
    model.to(device)
    # 损失函数
    criterion = nn.CrossEntropyLoss(reduction = "mean")
    # 损失函数对象放到gpu上
    criterion.to(device)
    # 用于分割的损失函数
    split_criterion = SCELoss(alpha=0.1, beta=1, num_classes=class_num)
    # 分割损失函数对象放到gpu上
    split_criterion.to(device)
    # semi 损失函数
    semi_criterion = MixMatchLoss(rampup_length=120, lambda_u=15) # rampup_length = 120  same as epoches
    # 损失函数对象放到gpu上
    semi_criterion.to(device)
    # 优化器
    optimizer = torch.optim.Adam(model.parameters(), lr = 0.002)
    # 先选择clean seed
    # clean seed samples
    clean_data_info = {}
    all_data_info = {}
    for class_idx in range(class_num):
        clean_data_info[class_idx] = []
        all_data_info[class_idx] = []
    for idx, item in enumerate(poisoned_train_dataset):
        sample = item[0]
        label = item[1]
        if idx not in poisoned_ids:
            clean_data_info[label].append(idx)
        all_data_info[label].append(idx)
    # 选出的clean seed idx
    choice_clean_indice = []
    for class_idx, idx_list in clean_data_info.items():
        # 从每个class_idx中选择10个sample idx
        choice_list = np.random.choice(idx_list, replace=False, size=10).tolist()
        choice_clean_indice.extend(choice_list)
        # 从all_data_info中剔除选择出的clean seed sample index
        all_data_info[class_idx] = [x for x in all_data_info[class_idx] if x not in choice_list]
    choice_clean_indice = np.array(choice_clean_indice)

    choice_num = 0
    best_acc = -1
    best_epoch = -1
    for epoch in range(120):
        print("===Epoch: {}/{}===".format(epoch, 120))
        if epoch < 60:
            record_list = poison_linear_record(model, poisoned_eval_dataset_loader, split_criterion, device)
            if epoch % 5 == 0 and epoch != 0:
                # 每五个epoch 就选择10个
                choice_num += 10
            print("Mining clean data by class-aware loss-guided split...")
            # 0表示在污染池,1表示在clean pool
            split_indice = class_aware_loss_guided_split(record_list, choice_clean_indice, all_data_info, choice_num, poisoned_ids)
            xdata = MixMatchDataset(poisoned_train_dataset, split_indice, labeled=True)
            udata = MixMatchDataset(poisoned_train_dataset, split_indice, labeled=False)
        elif epoch < 90:       
            record_list = poison_linear_record(model, poisoned_eval_dataset_loader, split_criterion, device)
            print("Mining clean data by class-agnostic loss-guided split...")
            split_indice = class_agnostic_loss_guided_split(record_list, 0.5, poisoned_ids)
            xdata = MixMatchDataset(poisoned_train_dataset, split_indice, labeled=True)
            udata = MixMatchDataset(poisoned_train_dataset, split_indice, labeled=False)
        elif epoch < 120:
            record_list = poison_linear_record(model, poisoned_eval_dataset_loader, split_criterion, device)
            meta_virtual_model = deepcopy(model)
            param_meta = [  
                            {'params': meta_virtual_model.layer3.parameters()},
                            {'params': meta_virtual_model.layer4.parameters()},
                            {'params': meta_virtual_model.linear.parameters()},
                            {'params': meta_virtual_model.classifier.parameters()}
                        ]
            # param_meta = [
            #                 {'params': meta_virtual_model.backbone.layer3.parameters()},
            #                 {'params': meta_virtual_model.backbone.layer4.parameters()},
            #                 {'params': meta_virtual_model.linear.parameters()}
            #             ]
            meta_optimizer = torch.optim.Adam(param_meta, lr=0.015)
            meta_criterion = nn.CrossEntropyLoss(reduction = "mean")
            meta_criterion.to(device)
            for _ in range(1):
                train_the_virtual_model(
                                        meta_virtual_model=meta_virtual_model, 
                                        poison_train_loader=poisoned_train_dataset_loader, 
                                        meta_optimizer=meta_optimizer,
                                        meta_criterion=meta_criterion,
                                        device = device
                                        )      
            meta_record_list = poison_linear_record(meta_virtual_model, poisoned_eval_dataset_loader, split_criterion, device)

            print("Mining clean data by meta-split...")
            split_indice = meta_split(record_list, meta_record_list, 0.5, poisoned_ids)

            xdata = MixMatchDataset(poisoned_train_dataset, split_indice, labeled=True)
            udata = MixMatchDataset(poisoned_train_dataset, split_indice, labeled=False)  

        # 开始clean pool进行监督学习,poisoned pool进行半监督学习    
        xloader = DataLoader(xdata,batch_size=64, num_workers=4, pin_memory=True, shuffle=True, drop_last=True)
        uloader = DataLoader(udata,batch_size=64, num_workers=4, pin_memory=True, shuffle=True, drop_last=True)
        print("MixMatch training...")
        # 半监督训练参数
        semi_mixmatch = {"train_iteration": 1024,"temperature": 0.5, "alpha": 0.75,"num_classes": class_num}
        poison_train_result = mixmatch_train(
            model,
            xloader,
            uloader,
            semi_criterion,
            optimizer,
            epoch,
            device,
            **semi_mixmatch
        )

        print("Test model on clean data...")
        clean_test_result = linear_test(
            model, clean_test_dataset_loader, criterion,device
        )

        print("Test model on poison data...")
        poison_test_result = linear_test(
            model, poisoned_test_dataset_loader, criterion,device
        )

        # if scheduler is not None:
        #     scheduler.step()
        #     logger.info(
        #         "Adjust learning rate to {}".format(optimizer.param_groups[0]["lr"])
        #     )

        # Save result and checkpoint.
        # 保存结果
        result = {
            "poison_train": poison_train_result,
            "clean_test": clean_test_result,
            "poison_test": poison_test_result,
        }
        
        create_dir(save_dir)
        save_file_name = f"result_epoch_{epoch}.data"
        save_file_path = os.path.join(save_dir, save_file_name)
        joblib.dump(result,save_file_path)
        print(f"epoch:{epoch},result: is saved in {save_file_path}")
        # result2csv(result, save_dir)
       
        # if scheduler is not None:
        #     saved_dict["scheduler_state_dict"] = scheduler.state_dict()

        is_best = False
        if clean_test_result["acc"] > best_acc:
            is_best = True
            best_acc = clean_test_result["acc"]
            best_epoch = epoch
         # 保存状态
        saved_dict = {
            "epoch": epoch,
            "result": result,
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "best_acc": best_acc, # clean testset上的acc
            "best_epoch": best_epoch,
        }
        print("Best test accuaracy {} in epoch {}".format(best_acc, best_epoch))
        # 每当best acc更新后，保存checkpoint
        ckpt_dir = os.path.join(save_dir, "ckpt")
        create_dir(ckpt_dir)
        if is_best:
            ckpt_path = os.path.join(ckpt_dir, "best_model.pt")
            torch.save(saved_dict, ckpt_path)
            print("Save the best model to {}".format(ckpt_path))
        # 保存最新一次checkpoint
        ckpt_path = os.path.join(ckpt_dir, "latest_model.pt")
        torch.save(saved_dict, ckpt_path)
        print("Save the latest model to {}".format(ckpt_path))
    print("asd_train() End")

def class_aware_loss_guided_split(record_list, choice_clean_indice, all_data_info, choice_num, poisoned_indice):
    """
    Adaptively split the poisoned dataset by class-aware loss-guided split.
    """
    keys = [record.name for record in record_list]
    loss = record_list[keys.index("loss")].data.numpy()
    # poison = record_list[keys.index("poison")].data.numpy()
    clean_pool_flag = np.zeros(len(loss))
    # 存总共选择的clean 的idx,包括seed和loss最底的sample idx
    total_indice = choice_clean_indice.tolist()
    for class_idx, sample_indice in all_data_info.items():
        # 遍历每个class_idx
        sample_indice = np.array(sample_indice)
        loss_class = loss[sample_indice]
        indice_class = loss_class.argsort()[: choice_num]
        indice = sample_indice[indice_class]
        total_indice += indice.tolist()
    # 统计构建出的clean pool 中还混有污染样本的数量
    poisoned_count = 0
    for idx in total_indice:
        if idx in poisoned_indice:
            poisoned_count+=1
    total_indice = np.array(total_indice)
    clean_pool_flag[total_indice] = 1

    print(
        "{}/{} poisoned samples in clean data pool".format(poisoned_count, clean_pool_flag.sum())
    )
    return clean_pool_flag

def class_agnostic_loss_guided_split(record_list, ratio, poisoned_indice):
    """
    Adaptively split the poisoned dataset by class-agnostic loss-guided split.
    """
    keys = [record.name for record in record_list]
    loss = record_list[keys.index("loss")].data.numpy()
    # poison = record_list[keys.index("poison")].data.numpy()
    clean_pool_flag = np.zeros(len(loss))
    total_indice = loss.argsort()[: int(len(loss) * ratio)]
    # 统计构建出的clean pool 中还混有污染样本的数量
    poisoned_count = 0
    for idx in total_indice:
        if idx in poisoned_indice:
            poisoned_count+=1
    print(
        "{}/{} poisoned samples in clean data pool".format(poisoned_count, len(total_indice))
    )
    clean_pool_flag[total_indice] = 1
    return clean_pool_flag

def meta_split(record_list, meta_record_list, ratio, poisoned_indice):
    """
    Adaptively split the poisoned dataset by meta-split.
    """
    keys = [record.name for record in record_list]
    loss = record_list[keys.index("loss")].data.numpy()
    meta_loss = meta_record_list[keys.index("loss")].data.numpy()
    # poison = record_list[keys.index("poison")].data.numpy()
    clean_pool_flag = np.zeros(len(loss))
    loss = loss - meta_loss
    total_indice = loss.argsort()[: int(len(loss) * ratio)]
    poisoned_count = 0
    for idx in total_indice:
        if idx in poisoned_indice:
            poisoned_count += 1
    print("{}/{} poisoned samples in clean data pool".format(poisoned_count, len(total_indice)))
    clean_pool_flag[total_indice] = 1
    return clean_pool_flag

def train_the_virtual_model(meta_virtual_model, poison_train_loader, meta_optimizer, meta_criterion, device):
    """
    Train the virtual model in meta-split.
    """
    meta_virtual_model.train()
    for batch_idx, batch in enumerate(poison_train_loader):
        data = batch[0]
        target = batch[1]
        data = data.to(device)
        target = target.to(device)
        # 优化器中的参数梯度清零
        meta_optimizer.zero_grad()
        output = meta_virtual_model(data)
        meta_criterion.reduction = "mean"
        # 损失函数
        loss = meta_criterion(output, target)
        # 损失函数对虚拟模型参数求导
        loss.backward()
        # 优化器中的参数更新
        meta_optimizer.step()

if __name__ == "__main__":
    main_test()