# async_base_repository.py
from __future__ import annotations
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Type, TypeVar, Union, Tuple
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from .base_repository_manager import BaseRepositoryManager

logger=logging.getLogger(__name__)
ModelType=TypeVar("ModelType")
CreateSchemaType=TypeVar("CreateSchemaType")
UpdateSchemaType=TypeVar("UpdateSchemaType")

class AsyncBaseRepository:
    model: Type[ModelType]=None
    def __init__(self, manager: BaseRepositoryManager, session_factory):
        self.manager=manager
        self.session_factory=session_factory
        self.session_scope=manager.session_scope
        if self.model is None: raise ValueError("Repository subclass must set 'model' attribute")

    async def create(self,obj_in:Union[CreateSchemaType,Dict[str,Any]])->ModelType:
        async with self.session_scope() as session:
            if hasattr(obj_in,"dict"): payload=obj_in.dict(exclude_unset=True)
            elif isinstance(obj_in,dict): payload=dict(obj_in)
            else: raise ValueError("obj_in must be dict or pydantic model")
            db_obj=self.model(**payload)
            if hasattr(db_obj,"created_at"): db_obj.created_at=datetime.utcnow()
            if hasattr(db_obj,"updated_at"): db_obj.updated_at=datetime.utcnow()
            session.add(db_obj)
            await session.flush()
            await session.refresh(db_obj)
            return db_obj

    async def get_by_id(self,id:Any,*,options:Optional[List[Any]]=None)->Optional[ModelType]:
        if id is None: return None
        async with self.session_scope() as session:
            stmt=select(self.model).where(self.model.id==id)
            if options: stmt=stmt.options(*options)
            res=await session.execute(stmt)
            return res.scalar_one_or_none()

    async def get_multi(self,*,filters:Optional[Dict[str,Any]]=None,order_by:Optional[Union[str,List[str]]]=None,limit:int=100,offset:int=0,options:Optional[List[Any]]=None)->Tuple[List[ModelType],int]:
        filters=filters or {}
        async with self.session_scope() as session:
            stmt=select(self.model)
            count_stmt=select(func.count()).select_from(self.model)
            for key,value in filters.items():
                if hasattr(self.model,key):
                    col=getattr(self.model,key)
                    if value is None: stmt=stmt.where(col.is_(None)); count_stmt=count_stmt.where(col.is_(None))
                    elif isinstance(value,(list,tuple,set)): stmt=stmt.where(col.in_(value)); count_stmt=count_stmt.where(col.in_(value))
                    else: stmt=stmt.where(col==value); count_stmt=count_stmt.where(col==value)
            if order_by:
                if isinstance(order_by,str): order_by=[order_by]
                order_clauses=[]
                for f in order_by:
                    if not f: continue
                    desc=f.startswith("-")
                    col_name=f[1:] if desc else f
                    if hasattr(self.model,col_name):
                        col=getattr(self.model,col_name)
                        order_clauses.append(col.desc() if desc else col.asc())
                if order_clauses: stmt=stmt.order_by(*order_clauses)
            if limit>0: stmt=stmt.offset(offset).limit(limit)
            if options: stmt=stmt.options(*options)
            result=await session.execute(stmt)
            items=result.scalars().all()
            if limit>0 and offset==0 and len(items)<limit: total=len(items)
            else:
                cnt_res=await session.execute(count_stmt)
                total=cnt_res.scalar() or 0
            return items,total

    async def update(self,*,db_obj:ModelType,obj_in:Union[UpdateSchemaType,Dict[str,Any]])->ModelType:
        async with self.session_scope() as session:
            if hasattr(obj_in,"dict"): data=obj_in.dict(exclude_unset=True)
            else: data={k:v for k,v in dict(obj_in).items() if v is not None}
            for k,v in data.items():
                if hasattr(db_obj,k): setattr(db_obj,k,v)
            if hasattr(db_obj,"updated_at"): db_obj.updated_at=datetime.utcnow()
            session.add(db_obj)
            await session.flush()
            await session.refresh(db_obj)
            return db_obj

    async def delete(self,*,id:Any,hard_delete:bool=False)->bool:
        async with self.session_scope() as session:
            if not hard_delete and hasattr(self.model,"is_deleted"):
                stmt=update(self.model).where(self.model.id==id).values(is_deleted=True)
                res=await session.execute(stmt)
                return res.rowcount>0
            else:
                stmt=delete(self.model).where(self.model.id==id)
                res=await session.execute(stmt)
                return res.rowcount>0

    async def exists(self,**filters:Any)->bool:
        async with self.session_scope() as session:
            stmt=select(self.model).limit(1)
            for key,value in filters.items():
                if hasattr(self.model,key): stmt=stmt.where(getattr(self.model,key)==value)
            res=await session.execute(stmt)
            return res.scalars().first() is not None

    async def count(self,**filters:Any)->int:
        async with self.session_scope() as session:
            stmt=select(func.count()).select_from(self.model)
            for key,value in filters.items():
                if hasattr(self.model,key): stmt=stmt.where(getattr(self.model,key)==value)
            res=await session.execute(stmt)
            return int(res.scalar() or 0)
