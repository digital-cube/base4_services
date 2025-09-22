from typing import Any, Dict, List, Literal, Optional

import pydantic


class UniversalTableGetRequest(pydantic.BaseModel):
    profile: Optional[None | str] = None
    response_format: Optional[Literal['table', 'objects', 'key-value']] = 'objects'

    key_value_response_format_key: Optional[None | str] = None

    # header: Optional[bool] = True
    only_data: Optional[bool] = False
    meta: Optional[bool] = True
    fields: Optional[List[str] | str] = None  # None is all Ω
    # dict_filters: Optional[Dict[str, str]] = None
    filters: Optional[None | str] = None

    v3_filters: Optional[None | Any] = None

    search: Optional[None | str] = None

    v3f_statuses: Optional[None | str] = None
    v3f_priorities: Optional[None | str] = None
    v3f_service_groups: Optional[None | str] = None  # ?
    v3f_departments: Optional[None | str] = None
    v3f_assigned_to: Optional[None | str] = None
    v3f_customers: Optional[None | str] = None
    v3f_types: Optional[None | str] = None
    v3f_sites: Optional[None | str] = None
    v3f_created_by: Optional[None | str] = None
    v3f_last_updated_by: Optional[None | str] = None

    order_by: Optional[str] = None
    page: Optional[int] = 1
    per_page: Optional[int] = 100


class Column(pydantic.BaseModel):
    field: str
    name: str
    widths: Optional[List[int] | None] = None
    sortable: Optional[bool] = False
    filterable: Optional[bool | Dict] = False
    type: Optional[str] = 'string'
    display_name: Optional[str] = None
    align_text: Optional[Literal['start', 'end', 'center']] = 'start'


class Summary(pydantic.BaseModel):
    count: int

    page: int
    per_page: int
    total_pages: int


class BulkActions(pydantic.BaseModel):
    enabled: Optional[bool|None] = None
    method: Optional[None|Literal['POST','PUT','PATCH','GET']] = None
    url: Optional[None|str] = None

class Action(pydantic.BaseModel):
    name: Optional[None|str] = None
    icon: Optional[None|str] = None
    method: Optional[None|str] = None
    url: Optional[None|str] = None
    target: Optional[bool|str] = None

class Header(pydantic.BaseModel):
    columns: List[Column]
    summary: Summary
    response_format: Literal['table', 'objects', 'key-value'] = 'objects'
    # response_format: Optional[Literal['table', 'objects']] = 'objects'

    bulk_actions: Optional[None | BulkActions] = None
    actions: Optional[List[Action]] = None


class UniversalTableResponse(pydantic.BaseModel):
    header: Header
    data: List


class UniversalTableResponseBaseSchema(pydantic.BaseModel):
    @classmethod
    def build(cls, model_item, item_schema, request: UniversalTableGetRequest) -> Dict[str, Any] | List[Any]:
        model_loc = item_schema.model_loc()

        if request.response_format == 'objects':
            res = {}
            for field in cls.order():
                try:
                    res[field] = eval(f'model_item.{model_loc[field]}')
                except KeyError as k_exc:
                    if 'actions' in str(k_exc):
                        continue
                    raise k_exc
                except Exception as e:
                    raise
        elif request.response_format == 'key-value':
            res = {}
            for field in cls.order():
                res[field] = eval(f'model_item.{model_loc[field]}')

        elif request.response_format == 'table':
            res = []
            for field in cls.order():
                res.append(eval(f'model_item.{model_loc[field]}'))
        else:
            raise NameError(f"Unknown response_format: {request.response_format}")

        return res

    @classmethod
    def header(cls, request: UniversalTableGetRequest, summary: Summary, response_format: Literal['objects', 'table', 'key-value'] = 'objects'):

        bulk_actions = None
        actions = None

        if hasattr(cls, 'bulk_actions'):
            bulk_actions = cls.bulk_actions()
        if hasattr(cls, 'actions'):
            actions = cls.actions()

        res = Header(columns=[], summary=summary, response_format=response_format, bulk_actions=bulk_actions, actions=actions)
        widths = cls.column2width()
        justify = cls.column2justify()
        titles = cls.column2title()


        for field in cls.order():
            try:
                type_ = cls.model_fields[field].annotation.__name__

                column = Column(
                    name=field,
                    field=field,
                    type=cls.column_data_type(field) or type_,
                    sortable=cls.sortable(field),
                    filterable=cls.filter_properties(field),
                    widths=widths[field] if field in widths else None,
                    align_text=justify[field] if field in justify else 'start',
                    display_name=titles[field],
                )
                res.columns.append(column)
            except Exception as e:
                raise

        return res
