from __future__ import print_function
import sys 
sys.path.append("/home/guangyao_li/projects/avqa/music_avqa_camera_ready") 
import argparse
import torch
import numpy as np
torch.set_printoptions(threshold=np.inf, edgeitems=120, linewidth=120)
import torch.nn as nn
import torch.optim as optim
from dataloader_avst import *
from net_avst import AVQA_Fusion_Net
import ast
import json
import numpy as np
import pdb
import cv2 
import itertools
# from .net_avst import AVQA_Fusion_Net

import warnings
from datetime import datetime
TIMESTAMP = "{0:%Y-%m-%d-%H-%M-%S/}".format(datetime.now()) 
warnings.filterwarnings('ignore')
from torch.utils.tensorboard import SummaryWriter
writer = SummaryWriter('runs/net_avst/'+TIMESTAMP)
import time
import gc 
import logging


# logging level 
logging.basicConfig(level=logging.INFO)

torch.backends.cudnn.enabled = True
torch.backends.cudnn.benchmark = True

logging.info("\n--------------- Audio-Visual Spatial-Temporal Model --------------- \n")

def batch_organize(out_match_posi,out_match_nega):
    # audio B 512
    # posi B 512
    # nega B 512

    # logging.debug("audio data: ", audio_data.shape)
    out_match = torch.zeros(out_match_posi.shape[0] * 2, out_match_posi.shape[1])
    batch_labels = torch.zeros(out_match_posi.shape[0] * 2)
    for i in range(out_match_posi.shape[0]):
        out_match[i * 2, :] = out_match_posi[i, :]
        out_match[i * 2 + 1, :] = out_match_nega[i, :]
        batch_labels[i * 2] = 1
        batch_labels[i * 2 + 1] = 0
    
    return out_match, batch_labels


def train(args, model, train_loader, optimizer, criterion, epoch):
    model.train()
    total_qa = 0
    correct_qa = 0
    #start_time = time.time()
    for batch_idx, sample in enumerate(train_loader):
        audio, visual_posi,visual_nega, target, question, items = sample['audio'].to('cuda'), sample['visual_posi'].to('cuda'),sample['visual_nega'].to('cuda'), sample['label'].to('cuda'), sample['question'].to('cuda'), sample["items"].to("cuda")

        optimizer.zero_grad()
        out_qa, out_match_posi, out_match_nega, avgn_ce_loss, avgn_bce_loss_v, avgn_bce_loss_a, _ = model(audio, visual_posi, visual_nega, question, items)  

        out_match, match_label = batch_organize(out_match_posi, out_match_nega)  
        out_match, match_label = out_match.type(torch.FloatTensor).cuda(), match_label.type(torch.LongTensor).cuda()

        avgn_loss = avgn_ce_loss + avgn_bce_loss_v + avgn_bce_loss_a
    
        # output.clamp_(min=1e-7, max=1 - 1e-7)
        loss_match = criterion(out_match, match_label)
        loss_qa = criterion(out_qa, target)
        #print("out_qa",out_qa)
        #print("target",target)
        loss = loss_qa + 0.5 * loss_match + 0.5 * avgn_loss
        #loss = loss_qa +  0.5 * avgn_loss
        
        writer.add_scalar('run/match',loss_match.item(), epoch * len(train_loader) + batch_idx)
        writer.add_scalar('run/qa_test',loss_qa.item(), epoch * len(train_loader) + batch_idx)
        writer.add_scalar('run/avgn_loss',avgn_loss.item(), epoch * len(train_loader) + batch_idx)
        writer.add_scalar('run/avgn_ce_loss',avgn_ce_loss.item(), epoch * len(train_loader) + batch_idx)
        writer.add_scalar('run/avgn_bce_loss_v',avgn_bce_loss_v.item(), epoch * len(train_loader) + batch_idx)
        writer.add_scalar('run/avgn_bce_loss_a',avgn_bce_loss_a.item(), epoch * len(train_loader) + batch_idx)
        writer.add_scalar('run/both',loss.item(), epoch * len(train_loader) + batch_idx)


        loss.backward()
        optimizer.step()
        if batch_idx % args.log_interval == 0:
            logging.info('Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}'.format(
                epoch, batch_idx * len(audio), len(train_loader.dataset),
                       100. * batch_idx / len(train_loader), loss.item()))

        

def eval(model, val_loader,epoch):
    model.eval()
    total_qa = 0
    total_match=0
    correct_qa = 0
    correct_match=0
    with torch.no_grad():
        for batch_idx, sample in enumerate(val_loader):
            audio, visual_posi,visual_nega, target, question, items = sample['audio'].to('cuda'), sample['visual_posi'].to('cuda'),sample['visual_nega'].to('cuda'), sample['label'].to('cuda'), sample['question'].to('cuda'), sample["items"].to("cuda")

            out_qa, out_match_posi, out_match_nega, _, _, _, _ = model(audio, visual_posi,visual_nega, question)

            _, predicted = torch.max(out_qa.data, 1)
            total_qa += out_qa.size(0)
            correct_qa += (predicted == target).sum().item()

    logging.info('Accuracy qa: %.2f %%' % (100 * correct_qa / total_qa))
    writer.add_scalar('metri_qa',100 * correct_qa / total_qa, epoch)

    return 100 * correct_qa / total_qa


def test(model, val_loader):
    model.eval()
    total = 0 
    correct = 0
    samples = json.load(open('./data/json/avqa-test.json', 'r'))
    A_count = []
    A_cmp = []
    V_count = []
    V_loc = []
    AV_ext = []
    AV_count = []
    AV_loc = []
    AV_cmp = []
    AV_temp = []
    with torch.no_grad():
        for batch_idx, sample in enumerate(val_loader):
            audio, visual_posi,visual_nega, target, question, items = sample['audio'].to('cuda'), sample['visual_posi'].to('cuda'),sample['visual_nega'].to('cuda'), sample['label'].to('cuda'), sample['question'].to('cuda'), sample["items"].to("cuda")
            
            preds_qa,out_match_posi,out_match_nega,_,_,_,_ = model(audio, visual_posi,visual_nega, question)
            preds = preds_qa
            _, predicted = torch.max(preds.data, 1)

            total += preds.size(0)
            correct += (predicted == target).sum().item()

            x = samples[batch_idx]
            type =ast.literal_eval(x['type'])
            if type[0] == 'Audio':
                if type[1] == 'Counting':
                    A_count.append((predicted == target).sum().item())
                elif type[1] == 'Comparative':
                    A_cmp.append((predicted == target).sum().item())
            elif type[0] == 'Visual':
                if type[1] == 'Counting':
                    V_count.append((predicted == target).sum().item())
                elif type[1] == 'Location':
                    V_loc.append((predicted == target).sum().item())
            elif type[0] == 'Audio-Visual':
                if type[1] == 'Existential':
                    AV_ext.append((predicted == target).sum().item())
                elif type[1] == 'Counting':
                    AV_count.append((predicted == target).sum().item())
                elif type[1] == 'Location':
                    AV_loc.append((predicted == target).sum().item())
                elif type[1] == 'Comparative':
                    AV_cmp.append((predicted == target).sum().item())
                elif type[1] == 'Temporal':
                    AV_temp.append((predicted == target).sum().item())

    logging.info('Audio Counting Accuracy: %.2f %%' % (
            100 * sum(A_count)/len(A_count)))
    logging.info('Audio Cmp Accuracy: %.2f %%' % (
            100 * sum(A_cmp) / len(A_cmp)))
    logging.info('Audio Accuracy: %.2f %%' % (
            100 * (sum(A_count) + sum(A_cmp)) / (len(A_count) + len(A_cmp))))
    logging.info('Visual Counting Accuracy: %.2f %%' % (
            100 * sum(V_count) / len(V_count)))
    logging.info('Visual Loc Accuracy: %.2f %%' % (
            100 * sum(V_loc) / len(V_loc)))
    logging.info('Visual Accuracy: %.2f %%' % (
            100 * (sum(V_count) + sum(V_loc)) / (len(V_count) + len(V_loc))))
    logging.info('AV Ext Accuracy: %.2f %%' % (
            100 * sum(AV_ext) / len(AV_ext)))
    logging.info('AV counting Accuracy: %.2f %%' % (
            100 * sum(AV_count) / len(AV_count)))
    logging.info('AV Loc Accuracy: %.2f %%' % (
            100 * sum(AV_loc) / len(AV_loc)))
    logging.info('AV Cmp Accuracy: %.2f %%' % (
            100 * sum(AV_cmp) / len(AV_cmp)))
    logging.info('AV Temporal Accuracy: %.2f %%' % (
            100 * sum(AV_temp) / len(AV_temp)))

    logging.info('AV Accuracy: %.2f %%' % (
            100 * (sum(AV_count) + sum(AV_loc)+sum(AV_ext)+sum(AV_temp)
                   +sum(AV_cmp)) / (len(AV_count) + len(AV_loc)+len(AV_ext)+len(AV_temp)+len(AV_cmp))))

    logging.info('Overall Accuracy: %.2f %%' % (
            100 * correct / total))

    return 100 * correct / total


# def visualize(model, val_loader):
#     from visual_net import resnet18
#     visual_net = resnet18(pretrained=True)
#     model.eval()
#     total = 0
#     correct = 0
#     with torch.no_grad():
#         for batch_idx, sample in enumerate(val_loader):
#             print(sample.keys())
#             audio, visual_posi, visual_nega, target, question = sample['audio'].to('cuda'), sample['video_s'].to('cuda'),sample['video_s'].to('cuda'), sample['label'].to('cuda'), sample['question'].to('cuda')
#             video_id = sample['video_id']
#             video_org = sample['pos_frame_org']
            
#             visual_posi = visual_net(visual_net)
#             print(video_org.shape)
#             _, _, _, _, _, _, av_atten = model(audio, visual_posi,visual_nega, question)

#             print("\n\nvideo name: ", video_id)
#             # print("video_org type: ", type(video_org))

#             # 正负样本交替的, 隔一个取一个mask
#             obj_localization = av_atten.detach().cpu().numpy()  # (2, 1, 196)
#             obj_localization = obj_localization[::2]            # (1, 1, 196)

#             posi_img_data = video_org                            # [1, 3, 224, 224]

#             obj_len = obj_localization.shape[0]
#             print("obj_len: ", obj_len)
#             for j in range(obj_len):
#                 print("obj: ", obj_localization.shape)
#                 map = obj_localization[j, :, :].squeeze()

#                 print("map: ", map.shape)
#                 map = (map-map.min()) / (map.max()-map.min())
#                 map=cv2.resize(map.reshape(14,14),(224,224))
#                 map=map/map.max()
#                 map=np.uint8(map*255)
#                 heatmap = cv2.applyColorMap(map, cv2.COLORMAP_JET)

#                 print("map type: ", type(map))

#                 current_img = posi_img_data[j].cpu().numpy()
#                 print("current_img type: ", type(current_img))
#                 current_img = cv2.resize(current_img, (224, 224))
#                 print("current_img: ", current_img.shape)

#                 result = heatmap * 0.4 + current_img * 0.6
 
#                 file_name = '%04d_' % batch_idx + '%04d_0' % j + '.jpg'
#                 print("file_name: ", file_name)
#                 if not os.path.exists('net_grd_avst/models_grd_vis/vis_h4_c6'):
#                     os.makedirs('net_grd_avst/models_grd_vis/vis_h4_c6', exist_ok=True)
#                 cv2.imwrite(os.path.join('net_grd_avst/models_grd_vis/vis_h4_c6', file_name), result)
                

def main():
    # Training settings
    parser = argparse.ArgumentParser(description='PyTorch Implementation of Audio-Visual Question Answering')

    parser.add_argument(
        "--audio_dir", type=str, default='/home/guangyao_li/dataset/avqa-features/feats/vggish', help="audio dir")
    # parser.add_argument(
    #     "--video_dir", type=str, default='/home/guangyao_li/dataset/avqa/avqa-frames-1fps', help="video dir")
    parser.add_argument(
        "--video_res14x14_dir", type=str, default='/home/guangyao_li/dataset/avqa-features/visual_14x14', help="res14x14 dir")
    
    parser.add_argument(
        "--label_train", type=str, default="./data/json/avqa-train-updated.json", help="train csv file")
    parser.add_argument(
        "--label_val", type=str, default="./data/json/avqa-test.json", help="val csv file")
    parser.add_argument(
        "--label_test", type=str, default="./data/json/avqa-val.json", help="test csv file")
    parser.add_argument(
        "--label_visualization", type=str, default="./data/json/avqa-val_real.json", help="visualization csv file")
    parser.add_argument(
        '--batch-size', type=int, default=8, metavar='N', help='input batch size for training (default: 16)')
    parser.add_argument(
        '--epochs', type=int, default=80, metavar='N', help='number of epochs to train (default: 60)')
    parser.add_argument(
        '--lr', type=float, default=1e-4, metavar='LR', help='learning rate (default: 3e-4)')
    parser.add_argument(
        "--model", type=str, default='AVQA_Fusion_Net', help="with model to use")
    parser.add_argument(
        "--mode", type=str, default='train', help="with mode to use")
    parser.add_argument(
        '--seed', type=int, default=1, metavar='S', help='random seed (default: 1)')
    parser.add_argument(
        '--log-interval', type=int, default=50, metavar='N', help='how many batches to wait before logging training status')
    parser.add_argument(
        "--model_save_dir", type=str, default='net_grd_avst/avst_models/', help="model save dir")
    parser.add_argument(
        "--checkpoint", type=str, default='avst_73.06', help="save model name")
    parser.add_argument(
        '--gpu', type=str, default='1', help='gpu device number')


    args = parser.parse_args()
    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    random.seed(args.seed)
    
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.enabled = True

    if args.model == 'AVQA_Fusion_Net':
        model = AVQA_Fusion_Net()
        model = model.to('cuda')
        #model = nn.DataParallel(model)
    else:
        raise ('not recognized')

    if args.mode == 'train':
        train_dataset = AVQA_dataset(label=args.label_train, audio_dir=args.audio_dir, video_res14x14_dir=args.video_res14x14_dir,
                                    transform=transforms.Compose([ToTensor()]), mode_flag='train')
        #train_loader = DataLoader(train_dataset, batch_size=ags.batch_size, shuffle=True, num_workers=8, pin_memory=True)
        train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, num_workers=4, pin_memory=True)
        val_dataset = AVQA_dataset(label=args.label_val, audio_dir=args.audio_dir, video_res14x14_dir=args.video_res14x14_dir,
                                    transform=transforms.Compose([ToTensor()]), mode_flag='val')
        val_loader = DataLoader(val_dataset, batch_size=32, shuffle=False, num_workers=4, pin_memory=True)


        # ===================================== load pretrained model ===============================================
        ####### concat model
        pretrained_file = "grounding_gen/models_grounding_gen/main_grounding_gen_best.pt"
        checkpoint = torch.load(pretrained_file)
        logging.info("\n-------------- loading pretrained models --------------")
        model_dict = model.state_dict()
        
        # tmp = ['module.fc_a1.weight', 'module.fc_a1.bias','module.fc_a2.weight','module.fc_a2.bias','module.fc_gl.weight','module.fc_gl.bias','module.fc1.weight', 'module.fc1.bias','module.fc2.weight', 'module.fc2.bias','module.fc3.weight', 'module.fc3.bias','module.fc4.weight', 'module.fc4.bias']
        # tmp2 = ['module.fc_a1.weight', 'module.fc_a1.bias','module.fc_a2.weight','module.fc_a2.bias']
        # pretrained_dict1 = {k: v for k, v in checkpoint.items() if k in tmp}
        # pretrained_dict2 = {str(k).split('.')[0]+'.'+str(k).split('.')[1]+'_pure.'+str(k).split('.')[-1]: v for k, v in checkpoint.items() if k in tmp2}
        
        for key, value in checkpoint.items():
            potential_key = '.'.join(key.split('.')[1:])
            if potential_key in model_dict:
                model_dict[potential_key] = value 
                logging.info("Successfully load layer {potential_key}.")
        model.load_state_dict(model_dict)

        logging.info("\n-------------- load pretrained models --------------")

        # ===================================== load pretrained model ===============================================

        optimizer = optim.Adam(model.parameters(), lr=args.lr)
        scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=16, gamma=0.3)
        criterion = nn.CrossEntropyLoss()
        best_acc = 0
        for epoch in range(1, args.epochs + 1):
            train(args, model, train_loader, optimizer, criterion, epoch=epoch)
            scheduler.step(epoch)
            logging.info(f"Current learning rate: {optimizer.param_groups[0]['lr']}")
            acc = eval(model, val_loader, epoch)
            if acc >= best_acc:
                best_acc = acc
                torch.save(model.state_dict(), args.model_save_dir + args.checkpoint + ".pt")
                logging.info(f"Checkpoint epoch {epoch} acc {acc} has been saved.")
                
    # elif args.mode == "visualize":
    #     val_dataset = AVQADatasetVis(label_data=args.label_visualization, audio_dir=args.audio_dir, video_dir=args.video_res14x14_dir)
    #     val_loader = DataLoader(val_dataset, batch_size=1, shuffle=False, num_workers=0, pin_memory=True)


    #     # ===================================== load pretrained model ===============================================
    #     logging.info("\n-------------- loading pretrained models --------------")
        
    #     pretrained_file = "net_grd_avst/avst_models/avst.pt"
    #     checkpoint = torch.load(pretrained_file)
        
    #     model.load_state_dict(checkpoint)
    #     model.eval() 

    #     logging.info("\n-------------- load pretrained models --------------")

    #     # ===================================== load pretrained model ===============================================

    #     visualize(model, val_loader)

    else:
        test_dataset = AVQA_dataset(label=args.label_test, audio_dir=args.audio_dir, video_res14x14_dir=args.video_res14x14_dir,
                                   transform=transforms.Compose([ToTensor()]), mode_flag='test')
        logging.debug(test_dataset.__len__())
        test_loader = DataLoader(test_dataset, batch_size=1, shuffle=False, num_workers=4, pin_memory=True)
        model.load_state_dict(torch.load(args.model_save_dir + args.checkpoint + ".pt"))
        test(model, test_loader)


if __name__ == '__main__':
    main()