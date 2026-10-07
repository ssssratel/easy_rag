'''
预处理调用
处理清洗后文本分段paras_before
处理调模型后单品种文本去重clean_lap_paras

'''
import re

width1 = 1000
width2 = 1250
lap_lenth = 100
rule1 = r"。\n\n+|。\n\s*[()（）]+[一二三四五六七八九十]+[()（）]+|。\n\s*\.[一二三四五六七八九十]+\.|。\n\s*[()（）]+\s*\d+[()（）]+|。\n\s*\d+\."
rule2 = r"。\n\n+|。\n\s*[()（）]+[一二三四五六七八九十]+[()（）]+|。\n\s*\.[一二三四五六七八九十]+\.|。\n\s*[()（）]+\s*\d+[()（）]+|。\n\s*\d+\.|\n\n|。"

def clean_lap_paras(text_list:list):
    """预留的重叠片段去重接口，当前尚未实现。"""

    return

def split_by_separators(text:str, separators:list, lap_lenth:int):
    """按分隔符寻找接近目标长度的末尾文本。"""
    result = []
    for sep in separators:
        if sep in text:
            # 按当前分隔符进行分割，获取分隔符前的部分
            part, text = text.split(sep, 1)
            result.append(part + sep)
        else:
            break
    result.append(text)
    # 从末尾开始的最新文本，找接近250字的部分
    local_text = result[-1]
    local_min = len(local_text)
    if local_min == lap_lenth:
        return local_text, local_min

    newest_abs = local_min - lap_lenth
    # newest_abs = abs(local_min - lap_lenth)
    # 从倒数第二项往左遍历，至少要满足lap_lenth才返回
    for txt in result[-2::-1]:
        local_text = txt + local_text
        local_min = len(local_text)
        now_abs = local_min - lap_lenth
        # now_abs = abs(local_min - lap_lenth)
        if newest_abs > now_abs and now_abs > 0:
            # if newest_abs > now_abs:
            # 当前位置比上一位置接近250字
            newest_abs = now_abs
            continue
        # 当前位置比上一位置更远离250字
        break
    return local_text, local_min


def pro_paras(text_list: list, paras: list):
    """把前一段的末尾内容重叠到后一段。"""
    # 已经分好段的文本找每段最后最接近lap_lenth的分隔符，分割后合并到下一段
    if len(text_list) == 1:
        return text_list
    lap_lenth, rule1, rule2 = paras
    new_paras = []
    last = text_list[0]
    need_extend = ''
    for idx, para_now in enumerate(text_list):
        # 第一段
        if idx == 0:
            new_paras.append(para_now)
            continue
        if idx == len(text_list) - 1:
            new_paras.append(need_extend + para_now)
            continue
        # 第二段及以后
        min_distance = None  # 是否已获取到最小距离的标点/分隔符
        need_text = None
        for rule in [rule1, rule2, '\n\n', '。']:
            once_separators = re.findall(rule, last)  # 找标点第一次
            if once_separators:
                # 获取上一段末尾当前分隔符规则下最接近250字的文本
                right_text, new_distance = split_by_separators(last, once_separators, lap_lenth)
                if new_distance == lap_lenth:
                    last = para_now
                    need_text = right_text
                    break
                # 当前最小值赋给min_distance
                if min_distance == None:
                    min_distance = new_distance
                    need_text = right_text
                else:
                    if min_distance < new_distance:
                        # 当前最近距离 比 上次最近距离远
                        continue
                    min_distance = new_distance
                    need_text = right_text
        last = para_now
        if need_text != None:
            need_extend = need_text
            new_paras.append(need_extend + para_now)
    return new_paras


def paras_before(text: str, paras1: list, lastlen=100):
    """按长度窗口和标点规则切分文本。"""
    width1, width2, lap_lenth, rule1, rule2 = paras1
    para_now = text[width1:width2]
    t_list = []
    while len(text) > width1:
        have_shit1 = re.findall(rule1, para_now)
        if have_shit1 != []:
            # print("shit1")
            sp_text = para_now.split(have_shit1[0], 1)[0]
            para_left = text[:width1] + sp_text + have_shit1[0]
            t_list.append(para_left)
            text = text[len(para_left):]
            para_now = text[width1:width2]
        else:
            have_shit2 = para_now.split("\n\n", 1)
            if len(have_shit2) != 1:
                # print("shit2")
                sp_text = have_shit2[0]
                para_left = text[:width1] + sp_text + "\n\n"
                t_list.append(para_left)
                text = text[len(para_left):]
                para_now = text[width1:width2]
            else:
                have_shit3 = para_now.split("。", 1)
                if len(have_shit3) == 1:
                    have_shit4 = re.findall(rule2, text[width2:])
                    if len(have_shit4) >= 1:
                        # print("have shit4")
                        para_left = text[:width2] + text[width2:].split(have_shit4[0], 1)[0] + have_shit4[0]
                        t_list.append(para_left)
                        text = text[len(para_left):]
                    else:
                        have_shit5 = text[:width1].split("。", 1)
                        if len(have_shit5) != 1:
                            # print("have shit5")
                            sp_text = have_shit5[0]
                            para_left = sp_text + "。"
                            t_list.append(para_left)
                            text = text[len(para_left):]
                            para_now = text[len(para_left):width2]
                        else:
                            # print("have shit55")
                            para_left = text[:width1]
                            t_list.append(para_left)
                            text = text[len(para_left):]
                            para_now = text[width1:width2]
                else:
                    # print("shit33")
                    para_left = text[:width1] + have_shit3[0] + "。"
                    t_list.append(para_left)
                    text = text[len(para_left):]
                    para_now = text[width1:width2]
    if len(text) < lastlen:
        last_para = t_list[-1]
        t_list[-1] = last_para + " " + text
    else:
        t_list.append(text)
    paras = [lap_lenth, rule1, rule2]
    t_list = pro_paras(t_list, paras)

    return t_list

if __name__ == '__main__':
    paras = [width1, width2, lap_lenth, rule1, rule2]
    text = ''
    chunks = paras_before(text, paras)
