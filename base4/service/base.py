import datetime
import importlib
import uuid
from typing import Any, Dict, Generic, List, Type, TypeVar, get_args, get_origin

import base4.schemas.universal_table as universal_table
import pydantic
import tortoise.fields
import tortoise.timezone
from base4.debug import debug_info
from base4.models.utils import find_field_in_q
from base4.schemas.base import NOT_SET
from base4.utilities.db.base import BaseServiceDbUtils
from base4.utilities.logging.setup import class_exception_traceback_logging, get_logger
from base4.utilities.parsers.str2q import transform_filter_param_to_Q
from base4.utilities.service.base import BaseServiceUtils
from base4.utilities.service.base_pre_and_post import BaseServicePreAndPostUtils
from base4.utilities.ws import emit, sio_client_manager
from fastapi import HTTPException, Response
from fastapi.requests import Request
from tortoise.queryset import Q
from tortoise.transactions import in_transaction

SchemaType = TypeVar('SchemaType')
ModelType = TypeVar('ModelType', bound=tortoise.models.Model)
C11Type = Type['C11Type']
C1NType = Type['C1NType']

from base4.schemas.universal_table import UniversalTableGetRequest, UniversalTableResponse

logger = get_logger()

sio_connection = sio_client_manager(write_only=True)


class BaseService[ModelType]:

    def __init__(
            self,
            schema: Type[SchemaType],
            model: Type[ModelType],
            conn_name: str,
            c11: Type[C11Type] = None,
            c1n: Type[C1NType] = None,
            uid_prefix='?',
            uid_total_length=10,
            uid_alphabet='WERTYUPASFGHJKLZXCVNM2345679',
    ):
        self.schema = schema
        self.model = model
        self.c11 = c11
        self.c1n = c1n
        self.uid_prefix = uid_prefix
        self.uid_total_length = uid_total_length
        self.uid_alphabet = uid_alphabet
        self.conn_name = conn_name
        self.sio_connection = sio_connection

        # type(field), type(field) == field_type),
        def find_field_types(_model, field_type, related_name):
            return [
                field_name
                for field_name, field in _model._meta.fields_map.items()
                if isinstance(field, field_type) and getattr(field, 'related_name', None) == related_name
            ]

        self.c11_related_to = None
        self.c1n_related_to = None

        self.base_table_name = self.model.Meta.table

        try:
            if self.c11:
                one_to_one_fields = find_field_types(self.c11, tortoise.fields.relational.OneToOneFieldInstance, 'cache11')

                if len(one_to_one_fields) != 1:
                    raise Exception(f"Expected exactly one OneToOneField in {self.c11.__name__} model.")

                self.c11_related_to = one_to_one_fields[0]

            if self.c1n:
                many_to_many_fields = find_field_types(self.c1n, tortoise.fields.relational.ManyToManyFieldInstance, 'cache1n')
                if not many_to_many_fields:
                    many_to_many_fields = find_field_types(self.c1n, tortoise.fields.relational.ForeignKeyFieldInstance, 'cache1n')

                if len(many_to_many_fields) != 1:
                    raise Exception(f"Expected exactly one ManyToManyField in {self.c1n.__name__} model.")

                self.c1n_related_to = many_to_many_fields[0]

        except Exception as e:
            print(e)
            raise

        ...

    def _get_opensearch_index(self):
        """Map model table names to OpenSearch indices"""
        table_to_index = {
            'tickets': 'tickets',
            'bp': 'bp', 
            'flow': 'flow',
            'timesheet': 'timesheet',
            'tenants_users': 'tenants_users'
        }
        return table_to_index.get(self.base_table_name)

    ...

    async def get_all(
            self, request: UniversalTableGetRequest, profile_schema: pydantic.BaseModel, _request: Request, post_process_method=None
    ) -> List | Dict | UniversalTableResponse:
        """
        Get all items from the table
        :param request: UniversalTableGetRequest object with parameters for filtering, sorting, pagination and response format
        :param profile_schema: Schema for the response format
        :return: List of items or UniversalTableResponse object
        """

        # Basic error handling

        # TODO: Vrati table kad budes sredio metu

        request.response_format = 'objects'

        if request.per_page < 1:
            raise HTTPException(status_code=400, detail={"code": "INVALID_PARAMETER", "parameter": "per_page", "message": "per_page must be greater than 0"})

        if request.page < 1:
            raise HTTPException(status_code=400, detail={"code": "INVALID_PARAMETER", "parameter": "page", "message": "page must be greater than 0"})

        if request.response_format == 'key-value':
            if not request.key_value_response_format_key:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "INVALID_PARAMETER", "message": "key_value_response_format_key must be set when response_format is key-value"},
                )

        if request.only_data and request.response_format != 'objects':
            raise HTTPException(
                status_code=400, detail={"code": "INVALID_PARAMETER", "message": "parameter only_data can be used only with objects response_format"}
            )

        # setup prefetch_related if needed

        prefetch_related = []
        if self.c11:
            prefetch_related.append('cache11')
        if self.c1n:
            prefetch_related.append('cache1n')

        # calculate offset based on page and per_page

        offset = (request.page - 1) * request.per_page

        # setup filters if filters are requested

        filters = None
        if request.filters:
            try:
                # convert filters string to Q object

                filters = transform_filter_param_to_Q(request.filters)
            except Exception as e:
                raise HTTPException(
                    status_code=400, detail={"code": "INVALID_PARAMETER", "parameter": "filters", "message": f"Invalid filter parameters {request.filters}"}
                )

        # if deleted not presented in filters, add it as deleted=False

        if not filters:
            # TODO: Tenant
            filters = Q(is_deleted=False)
        else:
            filters = eval(filters)
            d = find_field_in_q(filters, 'is_deleted')
            if not d:
                filters &= Q(is_deleted=False)

        if hasattr(self, 'specific_table_filtering'):
            try:
                spec_q = await self.specific_table_filtering(request)
            except Exception as e:
                raise

            filters &= spec_q


        if request.search:

            # if there is search file in c1n - force it with specific language
            # TODO:...

            # if there is search file in c11 - foree it
            # TODO:...

            # if there is search_term file in base table - use it
            # TODO:...

            # if noone of above, use default search
            # check if there is display_name and use it

            if 'display_name' in self.model._meta.fields_map.keys():
                filters &= Q(display_name__icontains=request.search)

        try:
            # build query

            query = self.model
            query = query.filter(filters) if filters else query.filter()
            query = query.prefetch_related(*prefetch_related)

            # save this state without offset and limit for counting total items if header is requested
            cquery = query

            if request.order_by:

                # TODO: Ovde nekako doci do profila i onda irkosiriti order_by metod da dobijemo koji je

                order_by = request.order_by

                # FIX - za sada

                minus = False
                if '-' in order_by:
                    minus = True
                    order_by = order_by[1:]

                if order_by in ('sla_deadline_for_open', 'sla_deadline_for_resolve'):
                    order_by = f'cache11__{order_by}'

                if minus:
                    order_by = f'-{order_by}'

                query = query.order_by(order_by)
            else:
                query = query.order_by('created')
            # apply offset and limit
            query = query.offset(offset).limit(request.per_page)

            items = await query.all()

        except Exception as e:
            raise HTTPException(status_code=500, detail={"code": "INTERNAL_SERVER_ERROR", "debug": debug_info(str(e)), "message": "Internal server error."})

        # extract window of items in response format

        async def ps_build(item, post_process_method=None):
            if post_process_method:
                await post_process_method(item)
            res = profile_schema.build(item, self.schema, request)

            if profile_schema.meta():
                meta = {}
                for key in profile_schema.meta()['__meta']:
                    meta[key] = eval(profile_schema.meta()['__meta'][key])
                res['meta'] = meta
                # res['meta'] = {'id': item.id, 'link': {'url': f'/admin/ticket/v2/{item.id}'}}

            return res

        try:
            _data = [await ps_build(item=item, post_process_method=post_process_method) for item in items]
        except Exception as e:
            raise

        # if only_data is requested, return only data

        if hasattr(profile_schema, 'post_get'):
            try:
                _data = await profile_schema.post_get(svc=self, data=_data, request=request, _request=_request)
            except Exception as e:
                raise

        if request.only_data:
            return _data

        # calculate total items count and total pages

        count = await cquery.count()

        summary = universal_table.Summary(
            count=count,
            page=request.page,
            per_page=request.per_page,
            total_pages=(await cquery.count() + request.per_page - 1) // request.per_page,
        )

        if request.response_format == 'key-value':
            try:
                return {str(item[request.key_value_response_format_key]): item for item in _data}
            except Exception as e:
                raise

        _header = profile_schema.header(request, summary, response_format=request.response_format)

        return UniversalTableResponse(data=_data, header=_header)

    @staticmethod
    async def _build(model, item):
        """
        Recursive build dict from model and item

        """

        model_loc = {}
        try:
            schema_class_loc = model.schema_class_loc()

            if hasattr(model, 'model_loc'):
                model_loc = model.model_loc()

        except Exception as e:
            raise

        res = {}

        for field in model.model_fields:

            if field in schema_class_loc:
                cls = schema_class_loc[field]

                if get_origin(cls) == list:
                    res[field] = []
                    try:
                        await item.fetch_related(field)
                    except Exception as e:
                        raise

                    for _item in eval(f'item.{model_loc[field]}'):
                        try:
                            res[field].append(await BaseService._build(cls.__args__[0], _item))
                        except Exception as e:
                            raise

                elif cls not in (str, bool, int, float, uuid.UUID, dict, datetime.date, datetime.datetime):
                    try:
                        if not BaseServiceUtils.has_attribute(item, model_loc[field]):
                            continue
                        try:
                            pl = await BaseService._build(cls, item)
                        except Exception as e:
                            raise e
                        if all(v is None for v in pl.values()):  # ako su svi None, preskoci
                            continue

                        res[field] = cls(**pl)
                    except Exception as e:
                        raise

                else:
                    # primitivan tip, uzima se direktno iz itema
                    if field in model_loc:
                        if not BaseServiceUtils.has_attribute(item, model_loc[field]):
                            continue
                        try:
                            res[field] = eval(f'item.{model_loc[field]}')
                        except Exception as e:
                            raise
                    else:
                        raise

        return res

    async def get_single_model(self, item_id, request: Request) -> ModelType:
        prefetch_related = []
        if self.c11:
            prefetch_related.append('cache11')
        if self.c1n:
            prefetch_related.append('cache1n')

        item = await self.model.filter(id=item_id, is_deleted=False).prefetch_related(*prefetch_related).get_or_none()

        if not item:
            raise HTTPException(status_code=404, detail=f"{self.base_table_name} with sent id doesn't exist.")

        return item

    async def mk_single_model(self, item: ModelType) -> SchemaType:
        try:

            pl = await BaseService._build(self.schema, item)
            res = self.schema(**pl)

        except Exception as e:
            raise

        return res

    async def get_single(self, item_id: uuid.UUID, request: Request) -> SchemaType:
        """
        Get single item from the table

        """

        try:
            item = await self.get_single_model(item_id, request)
        except Exception as e:
            raise

        if hasattr(self.schema, 'post_get'):
            try:
                await self.schema.post_get(svc=self, item=item, request=request)
            except Exception as e:
                raise

        return await self.mk_single_model(item)

    async def get_single_and_extract_field(self, item_id: uuid.UUID, field: str, request: Request):
        """
        Get single item from the table and extract field

        """

        item = await self.get_single_model(item_id, request)
        return getattr(item, field)

    async def get_single_field(self, item_id: uuid.UUID, field: str, request: Request) -> Any:
        """
        Get single field from the table

        """
        item = await self.get_single_model(item_id, request)
        return {'value': getattr(item, field)}

    async def update_if_exists_on_create(
            self,
            logged_user_id: uuid.UUID,
            payload: SchemaType,
            request: Request,
            update_if_exists_key_fields: List[str] = None,
            update_if_exists_value_fields: List[Any] = None,
    ):
        BaseServiceUtils.validate_update_if_exists_params(update_if_exists_key_fields, update_if_exists_value_fields)

        payload.last_updated_by = logged_user_id

        # TODO: add id_tenant in this query
        # also is_deleted should figure here, and ? if is_deleted should we undelete or create new?
        # ?
        #

        item = await self.model.filter(
            **{update_if_exists_key_fields[i]: update_if_exists_value_fields[i] for i in range(len(update_if_exists_key_fields))}
        ).get_or_none()

        if item:
            # TODO what with pre_save, post_save, post_commit
            # mk_cache? (it should be processed in update)

            # Vidi, mozda da se ni ne radi update samo get, jer se ovde uglavnom nista ne updateuje - ovo je slicno kao get_or_create.. pa
            # primeni tu logiku

            return await self.update(logged_user_id, item.id, payload, request, return_db_item=True)

    async def create(
            self,
            logged_user_id: uuid.UUID,
            payload: SchemaType,
            request: Request,
            update_if_exists: bool = False,
            update_if_exists_key_fields: List[str] = None,
            update_if_exists_value_fields: List[Any] = None,
            conn=None,
            return_db_object=False,
    ):

        # !Removing transaction (TODO: Fix this bug)
        conn = None

        if update_if_exists:
            res = await self.update_if_exists_on_create(
                update_if_exists_key_fields=update_if_exists_key_fields,
                update_if_exists_value_fields=update_if_exists_value_fields,
                payload=payload,
                logged_user_id=logged_user_id,
                request=request,
            )
            if res:
                if return_db_object:
                    return res
                else:
                    try:
                        return (await self.mk_single_model(res)).model_dump()
                    except:
                        return res

        BaseServiceUtils.update_payload_with_user_data(payload, logged_user_id)

        _id = await BaseServiceUtils.update_payload_with_ids(base_service_instance=self, payload=payload)

        body = {
            'id': _id,
            'created_by': payload.created_by,
            'last_updated_by': payload.last_updated_by,
        }

        BaseServiceUtils.update_body_with_timestamps(payload, body)

        m2m_relations = {}

        await BaseServicePreAndPostUtils.create_pre_save_hook(service_instance=self, payload=payload, request=request, body=body)

        try:
            item = await BaseServiceDbUtils.db_operations(
                base_service_instance=self, request=request, body=body, payload=payload, logged_user_id=logged_user_id, m2m_relations=m2m_relations, _conn=conn
            )
        except Exception as e:
            raise

        post_commit_result = await BaseServicePreAndPostUtils.create_post_save_hook(service_instance=self, payload=payload, request=request, item=item)

        await self.validate(logged_user_id, item.id, request, quiet=True)

        await self.create_activity_log(item=item, handler=request)
        if return_db_object:
            return item
        try:
            res = (await self.get_single(item_id=item.id, request=request)).model_dump()
            res["action"] = "created"
        except:
            res = {'id': item.id, 'action': 'created'}
        if post_commit_result and isinstance(post_commit_result, dict):
            res.update(post_commit_result)

        return res

    async def create_activity_log(self, item: ModelType, handler: Request):
        """this method should create an activity log, and should be overridden in child classes to customize behavior"""

    async def update_activity_log(self, item: ModelType, handler: Request, updated_fields: Dict[str, Any]):
        """this method should create an activity log, and should be overridden in child classes to customize behavior"""

    async def get_activity_log_header(self, item_id: uuid.UUID, handler: Request) -> dict:
        item = await self.get_single(item_id, handler)

        return {
            "created_by": item.created_by,
            "created_by_display_name": item.created_by_display_name,
            "last_updated_by": item.last_updated_by,
            "last_updated_by_display_name": item.last_updated_by_display_name,
        }

    async def validate(
            self,
            logged_user_id: uuid.UUID,
            item_id: uuid.UUID,
            request: Request,
            quiet: bool = False,
    ):

        item = await self.get_single_model(item_id, request)
        if not item:
            raise HTTPException(status_code=404, detail={'code': 'NOT_FOUND', 'message': f"{self.base_table_name} with sent id doesn't exist."})

        if not item.is_valid:
            item.is_valid = True
            await item.save()

        return {'valid': True}

    async def create_or_update(self, logged_user_id: uuid.UUID, key_id: List[Any], payload: SchemaType, request: Request, response: Response) -> Dict[str, Any]:

        key_id_dict = {}
        for key in key_id:
            if not hasattr(payload, key) or not getattr(payload, key):
                raise HTTPException(status_code=400, detail={"code": "INVALID_PARAMETER", "parameter": key, "message": f"Missing parameter {key}"})

            key_id_dict[key] = getattr(payload, key)

        existing = await self.model.filter(**key_id_dict).get_or_none()
        if not existing:
            res = await self.create(logged_user_id, payload, request, return_db_object=True)
            response.status_code = 201
            return {'action': 'created', 'id': res.id}

        res = await self.update(logged_user_id, existing.id, payload, request)
        return res

    async def update(self, logged_user_id: uuid.UUID, item_id: uuid.UUID, payload: SchemaType, request: Request, return_db_item=False):

        model_item = await self.get_single_model(item_id, request)

        schem_item = await self.mk_single_model(model_item)

        model_loc = self.schema.model_loc()

        payload.last_updated_by = logged_user_id

        await BaseServicePreAndPostUtils.update_pre_save_hook(service_instance=self, payload=payload, request=request, item=model_item)

        if (
                updated := await BaseServiceUtils.update_db_entity_instance(
                    model_loc=model_loc,
                    payload=payload,
                    db_item=model_item,
                    schem_item=schem_item,
                    service_instance=self,
                    request=request,
                    logged_user_id=logged_user_id,
                )
        ) != {}:
            await model_item.save()

            await self.validate(logged_user_id, item_id, request, quiet=False)

            if self.c11:
                try:
                    await self.mk_cache(request, 'c11', model_item.cache11, model_item)
                except Exception as e:
                    raise
            if self.c1n:
                ...
                # TODO: Update cache for c1n

            await BaseServiceUtils.update_updated_fields(
                request=request, model_item=model_item, updated=updated, schem_item=schem_item, service_instance=self, logged_user_id=logged_user_id
            )

            await self.update_activity_log(model_item, request, updated)

        if return_db_item:
            return model_item

        res = {'id': str(item_id), 'updated': updated, 'action': 'updated' if updated else 'no_changes'}

        post_commit_update_result_wrapped = await BaseServicePreAndPostUtils.update_post_save_hook(
            service_instance=self,
            payload=payload,
            request=request,
            item=model_item,
            updated=updated,
        )

        # OpenSearch synchronization
        if updated:  # Only sync if there were actual changes
            try:
                index = self._get_opensearch_index()
                if index:
                    from shared.opensearch.populate_opensearch import create_opensearch_model, update_opensearch_instance
                    opensearch_data = await create_opensearch_model(model_item)
                    await update_opensearch_instance(index, opensearch_data, str(item_id))
            except Exception as e:
                from base4.utilities.logging.setup import get_logger
                logger = get_logger()
                logger.warning(f"OpenSearch UPDATE sync failed for {self.base_table_name}/{item_id}: {str(e)}")

        if isinstance(post_commit_update_result_wrapped, tuple) and len(post_commit_update_result_wrapped) == 2:
            wrap = post_commit_update_result_wrapped[1]
            post_commit_update_result = post_commit_update_result_wrapped[0]
        else:
            post_commit_update_result = post_commit_update_result_wrapped
            wrap = True

        if post_commit_update_result_wrapped is not None:
            # TODO ?!? smisli nesto univerzalnije
            if wrap:
                res['item'] = post_commit_update_result
            else:
                return post_commit_update_result
        return res

    async def delete(self, logged_user_id: uuid.UUID, item_id: uuid.UUID, request: Request):

        model_item = await self.get_single_model(item_id, request)

        model_item.is_deleted = True
        model_item.deleted_by = logged_user_id
        model_item.deleted = datetime.datetime.now()

        await model_item.save()

        # OpenSearch synchronization
        try:
            index = self._get_opensearch_index()
            if index:
                from shared.opensearch.populate_opensearch import delete_opensearch_instance
                await delete_opensearch_instance(index, str(item_id))
        except Exception as e:
            from base4.utilities.logging.setup import get_logger
            logger = get_logger()
            logger.warning(f"OpenSearch DELETE sync failed for {self.base_table_name}/{item_id}: {str(e)}")

        return

    async def mk_cache(self, request: Request, cache_type, citem, item, conn=None, ):

        from base4.project_specifics import lookups_module

        import shared.ipc as ipc  # DO NOT REMOVE THIS IMPORT, IT IS USED BY EVAL FUNCTION

        # lookups = lookups_module.Lookups

        updated = set()
        for c in citem.mk_cache_rules:
            if 'column' not in c:
                continue
            if 'method' not in c:
                continue

            current = getattr(citem, c['column'])

            if c['method'] == 'copy_from_base_table':
                if 'source_column' not in c:
                    continue

                new_value = getattr(item, c['source_column'])

                if current != new_value:
                    setattr(citem, c['column'], new_value)
                    updated.add(c['column'])

            elif c['method'] == 'copy_from_service_table':
                if 'service_module' not in c:
                    raise NameError("service_module must be used")

                if 'service_class' not in c:
                    raise NameError("service_class must be used")

                if 'service_class_method' not in c:
                    raise NameError("service_class_method source must be used")

                if 'args' not in c:
                    raise NameError("args must be used")

                try:
                    service_module = importlib.import_module(c['service_module'])
                    service_class = getattr(service_module, c['service_class'])
                    service_class_method = getattr(service_class, c['service_class_method'])

                    args = [eval(x) for x in c['args']]
                    new_value = await service_class_method(*args)

                    if current != new_value:
                        setattr(citem, c['column'], new_value)
                        updated.add(c['column'])

                except Exception as e:

                    raise

            elif c['method'] == 'async_method':
                if 'function' not in c:
                    raise NameError("Function must be used")

                try:
                    new_value = await eval(c['function'])
                except Exception as e:
                    raise

                if current != new_value:
                    if current != new_value:
                        setattr(citem, c['column'], new_value)
                        updated.add(c['column'])

            elif c['method'] == 'lookup':
                if 'target' not in c:
                    raise NameError("Target must be used")
                if 'source' not in c:
                    raise NameError("Source must be used")

                rlookup = lookups_module.LookupsReversed.get_instance()

                if getattr(citem, c['source']):
                    try:
                        lkp = rlookup[str(getattr(citem, c['source']))]
                    except Exception as e:
                        raise

                    if c['target'] not in ('code',):
                        raise NameError(f"Invalid target {c['target']}")

                    new_value = lkp[c['target']]
                else:
                    new_value = None

                if current != new_value:
                    setattr(citem, c['column'], new_value)
                    updated.add(c['column'])

            elif c['method'] == 'ipc':
                if 'ipc' not in c:
                    raise NameError("IPC must be used")

                if 'condition' in c:
                    if not eval(c['condition']):
                        continue

                try:
                    new_value = await eval(c['ipc'])
                except Exception as e:
                    new_value = None
                    raise

                if 'extract_key' in c and isinstance(new_value, dict):
                    try:
                        new_value = new_value[c['extract_key']] if c['extract_key'] in new_value else None
                    except Exception as e:
                        raise

                if current != new_value:
                    setattr(citem, c['column'], new_value)
                    updated.add(c['column'])

            # TODO: REMOVE THIS AND REPLACE WITH IPC
            elif c['method'] == 'svc_get':
                if 'service' not in c:
                    raise NameError("Service must be used")
                if 'uri' not in c and 'ipc' not in c:
                    raise NameError("Either uri or ipc must be used")
                if 'uri' in c and 'ipc' in c:
                    raise NameError("Only one of uri or ipc can be used")
                if 'source' not in c:
                    raise NameError("Source must be used")

                if not eval(c['source']):
                    new_value = None
                elif 'ipc' in c:
                    try:
                        new_value = await eval(c['ipc'])
                    except Exception as e:
                        raise

                if current != new_value:
                    setattr(citem, c['column'], new_value)
                    updated.add(c['column'])

        if updated:
            await citem.save(using_db=conn)
