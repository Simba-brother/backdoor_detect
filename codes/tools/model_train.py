import sys
sys.path.append("./")
import os
import random
import numpy as np
import torch
from torch.utils.data import DataLoader
from codes import utils

class ModelTrain(object):
    def __init__(self, model, transform_train, transform_test, trainset, testset, batch_size, epochs, device, loss_fn, optimizer, work_dir, scheduler):
        self.model = model
        self.transform_train = transform_train
        self.transform_test = transform_test
        self.trainset = trainset
        self.testset = testset
        self.batch_size = batch_size
        self.device = device
        self.loss_fn = loss_fn
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.epochs = epochs
        self.work_dir = work_dir
        
    def _random_seed(self):
        worker_seed = 666
        random.seed(worker_seed)
        np.random.seed(worker_seed)
        torch.manual_seed(worker_seed)
        deterministic = True

    def train(self):
        trainset_loader = DataLoader(
            self.trainset,
            batch_size = self.batch_size,
            shuffle=True,
            # num_workers=self.current_schedule['num_workers'],
            drop_last=False,
            pin_memory=False,
            worker_init_fn=self._random_seed()
            )
        best_acc = 0
        self.model.to(self.device)
        for epoch in range(self.epochs):
            print('Epoch: %d' % epoch)
            self.model.train()
            train_loss = 0
            correct = 0
            total = 0
            for batch_idx, (inputs, targets) in enumerate(trainset_loader):
                inputs, targets = inputs.to(self.device), targets.to(self.device)
                self.optimizer.zero_grad()
                outputs = self.model(inputs)
                loss = self.loss_fn(outputs, targets)
                loss.backward()
                self.optimizer.step()

                train_loss += loss.item() # 每个batch的累计损失
                _, predicted = outputs.max(1)
                total += targets.size(0)
                correct += predicted.eq(targets).sum().item()
                utils.progress_bar(batch_idx, len(trainset_loader), 'Loss: %.3f | Acc: %.3f%% (%d/%d)'
                            % (train_loss/(batch_idx+1), 100.*correct/total, correct, total))
            epoch_acc = round(correct/total,3)
            print(f"epoch_acc:{epoch_acc}")
            if epoch_acc > best_acc:
                best_acc = epoch_acc
                utils.create_dir(self.work_dir)
                ckpt_model_path = os.path.join(self.work_dir, "best_model.pth")
                torch.save(self.model.state_dict(), ckpt_model_path)
                print(f"best model is saved in {ckpt_model_path}")
            self.scheduler.step()
    def test(self):
        testset_loader = DataLoader(
            self.testset,
            batch_size = self.batch_size,
            shuffle=False,
            # num_workers=self.current_schedule['num_workers'],
            drop_last=False,
            pin_memory=False,
            worker_init_fn=self._random_seed()
            )
        self.model.to(self.device)
        self.model.eval()
        test_loss = 0
        correct = 0
        total = 0
        with torch.no_grad():
            for batch_idx, (inputs, targets) in enumerate(testset_loader):
                inputs, targets = inputs.to(self.device), targets.to(self.device)
                outputs = self.model(inputs)
                loss = self.loss_fn(outputs, targets)

                test_loss += loss.item()
                _, predicted = outputs.max(1)
                total += targets.size(0)
                correct += predicted.eq(targets).sum().item()

                utils.progress_bar(batch_idx, len(testset_loader), 'Loss: %.3f | Acc: %.3f%% (%d/%d)'
                            % (test_loss/(batch_idx+1), 100.*correct/total, correct, total))
        acc = round(correct/total,3)
        print(f"acc:{acc}, {correct}/{total}")
        return acc