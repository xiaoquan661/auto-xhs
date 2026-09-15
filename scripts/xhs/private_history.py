"""Read message history through the signed-in page's existing IM client."""

from __future__ import annotations

from typing import Any

_READ_HISTORY = r"""(async () => {
  const chunks = window.webpackChunkxhs_pc_web;
  if (!chunks) return {available:false,reason:'私信客户端尚未就绪'};
  let require = window.__autoXhsIMRequire;
  if (!require) {
    chunks.push([['auto-xhs-im-reader'],{},r => {require=r;}]);
    window.__autoXhsIMRequire = require;
  }
  let service;
  for (const module of Object.values(require.c)) {
    for (const value of Object.values(module.exports || {})) {
      if (typeof value === 'function' && value.instance &&
          typeof value.prototype?.getMessageRepository === 'function' &&
          typeof value.prototype?.getChatRepository === 'function') {
        service = value.instance; break;
      }
    }
    if (service) break;
  }
  if (!service?.sdk || !service.currentUserId)
    return {available:false,reason:'私信客户端尚未登录'};
  const sdk = service.sdk, owner = service.currentUserId;
  const conversations = new Map(), messages = new Map(), batches = [];
  let listComplete = false, allComplete = true, reason = '', nextTs;
  for (let page=0; page<6; page++) {
    const response = await sdk.getChatList({
      limit:100,complete:page===0,page,next_ts:nextTs});
    const rows = response?.data?.chats;
    const success = response?.success === true || response?.code === 0;
    batches.push({channel:'conversations',page,count:rows?.length,
      success:response?.success,code:response?.code,
      total:response?.data?.total,nextTs:response?.data?.nextTs});
    if (!success || !Array.isArray(rows)) {
      reason='会话列表未返回有效结果'; break;
    }
    for (const row of rows) conversations.set(row.chatUserId,row);
    if (conversations.size > LIMIT) {reason='达到本轮会话数量上限';break;}
    const total=response.data.total, next=response.data.nextTs;
    if (typeof total==='number' && conversations.size>=total && next===-1) {
      listComplete=true;break;
    }
    if (rows.length===0) {
      listComplete=typeof total!=='number' || conversations.size>=total;
      break;
    }
    if (next===-1) {reason='会话数量与平台总数尚未对齐';break;}
    nextTs=next;
  }
  for (const chat of [...conversations.values()].slice(0,LIMIT)) {
    const userId=chat.chatUserId;
    let cursor=Number(chat.maxStoreId || 0), complete=false;
    for (let page=0; page<20; page++) {
      const response = await sdk.getChatMessageHistory({
        chatUserId:userId,lastId:cursor,startId:0,limit:30});
      const rows = response?.data;
      const success=response?.success===true || response?.code===0;
      batches.push({channel:'messages',user_id:userId,page,cursor,
        count:rows?.length,success:response?.success,code:response?.code});
      if (!success || !Array.isArray(rows)) {
        reason='消息历史未返回有效结果';break;
      }
      if (page===0 && cursor>0 && rows.length===0) {
        reason='会话存在历史消息，本轮历史读取为空';break;
      }
      for (const row of rows) {
        let body = row.content;
        try {body=typeof body==='string'?JSON.parse(body):body;} catch (_) {body={};}
        const type=Number(body?.contentType ?? body?.content_type ?? -1);
        if ([0,4,5].includes(type)) continue;
        let content=body?.content;
        try {content=typeof content==='string'?JSON.parse(content):content;} catch (_) {}
        const text=typeof content==='string'?content:content?.content || content?.text || '';
        const key=String(row.id || row.uuid || '');
        messages.set(userId+':'+key,{user_id:userId,
          platform_message_id:key,role:row.senderId===owner?'self':'peer',
          content:text || '[消息类型 '+type+']', content_type:type,
          occurred_at:row.createdAt,store_id:row.storeId});
      }
      if (rows.length<30) {complete=true;break;}
      const ids=rows.map(r=>Number(r.storeId)).filter(id=>id>0);
      const next=ids.length?Math.min(...ids)-1:-1;
      if(next<0 || next>=cursor) {reason='消息分页游标未推进';break;}
      cursor=next;
    }
    if(!complete) allComplete=false;
  }
  const complete=listComplete && allComplete;
  return {available:true,owner_uid:owner,complete,
    conversations:[...conversations.keys()].slice(0,LIMIT),
    messages:[...messages.values()],batches,
    reason:complete?'':reason || '本轮消息历史尚未采齐'};
})()"""


def collect_message_history(page: Any, max_conversations: int) -> dict:
    return page.evaluate(_READ_HISTORY.replace("LIMIT", str(max_conversations)))
