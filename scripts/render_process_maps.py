"""Offline directed process maps from frozen events."""
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scripts.benchlib.core import COLORS,read_json,write_json

def render(snapshot):
    out=snapshot/'exports';events=pd.read_csv(out/'events.csv');charts=[]
    for model,group in events.groupby('model'):
        group=group[group.event.isin(['llm_request','tool_result'])].copy()
        group['node']=group.apply(lambda r:('LLM:'+str(r['role'])) if r.event=='llm_request' else 'tool:'+str(r.tool),axis=1)
        group.loc[group.node.str.contains('<|',regex=False),'node']='tool:INVALID_NAME'
        edges={}
        for _,run in group.groupby('run_id',sort=False):
            seq=list(run.node)
            for a,b in zip(seq,seq[1:]):edges[(a,b)]=edges.get((a,b),0)+1
        top=sorted(edges.items(),key=lambda item:item[1],reverse=True)[:18]
        nodes=sorted({n for edge,_ in top for n in edge})
        if not nodes:continue
        positions={n:(np.cos(i*2*np.pi/len(nodes)),np.sin(i*2*np.pi/len(nodes))) for i,n in enumerate(nodes)}
        fig,ax=plt.subplots(figsize=(13,10))
        for (a,b),count in top:
            x,y=positions[a];xx,yy=positions[b]
            if a==b:continue
            ax.annotate('',xy=(xx,yy),xytext=(x,y),arrowprops={'arrowstyle':'->','alpha':.5,'lw':1+count/max(n for _,n in top)*3,'connectionstyle':'arc3,rad=.13'})
            ax.text((x+xx)/2,(y+yy)/2,str(count),fontsize=8,bbox={'facecolor':'white','alpha':.7,'edgecolor':'none'})
        for n,(x,y) in positions.items():
            ax.text(x,y,n,ha='center',va='center',fontsize=9,bbox={'boxstyle':'round,pad=.5','facecolor':COLORS.get(model,'#DDDDDD'),'edgecolor':'black'})
        ax.set_xlim(-1.6,1.6);ax.set_ylim(-1.4,1.4);ax.axis('off');ax.set_title('Карта переходов: '+model+'\nНаблюдаемые действия; стрелка — число переходов')
        name='event_map_'+model.replace('/','__')
        for ext in ['png','svg']:fig.savefig(out/(name+'.'+ext),dpi=160,bbox_inches='tight')
        plt.close(fig);charts.append(name)
    manifest=read_json(out/'export_manifest.json');manifest['charts']=sorted(set(manifest['charts']+charts));write_json(out/'export_manifest.json',manifest)
    return charts
