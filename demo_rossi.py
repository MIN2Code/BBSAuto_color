"""重建洛茜演示会话：56 件 STL + 3 张渲染图吸色 + 件级配色 + 正面投影上色。"""
import sys, glob
sys.path.insert(0, '.')
import httpx

BASE = 'http://127.0.0.1:8761/api'
c = httpx.Client(timeout=600)
sid = c.post(f'{BASE}/sessions').json()['session_id']

stls = sorted(glob.glob(r'J:/baidu/Remy - Endfield - rossi 明日方舟终末地 洛茜/pieces/*.stl'))
files = [('files', (f.replace(chr(92), '/').split('/')[-1], open(f, 'rb'))) for f in stls]
j = c.post(f'{BASE}/sessions/{sid}/parts', files=files).json()
print('导入件:', len(j['added']))

for png in ('Rossi01-mU7f.png', 'Rossi03-LfBe.png', 'Rossi05-jTbP.png'):
    c.post(f'{BASE}/sessions/{sid}/image',
           files={'file': (png, open(r'J:/baidu/Remy - Endfield - rossi 明日方舟终末地 洛茜/renders/' + png, 'rb').read())})
c.post(f'{BASE}/sessions/{sid}/auto')

s = c.get(f'{BASE}/sessions/{sid}').json()
import numpy as np
allmin = np.min([p['bbox'][0] for p in s['parts']], axis=0).tolist()
allmax = np.max([p['bbox'][1] for p in s['parts']], axis=0).tolist()
ctr = [(a + b) / 2 for a, b in zip(allmin, allmax)]
# 一期件级架构：正面投影改由前端"转台取色"完成（全局姿态拟合+件ID缓冲）
print('SID=' + sid)
