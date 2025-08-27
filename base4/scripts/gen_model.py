import datetime
import os

import yaml


def gen_model(tbl, tbl_name, ctable=False, parent_class_name='', parent_types=None):
    cls_name = tbl["__meta"].get("model_name")
    if not cls_name:
        cls_name = tbl_name.capitalize() if tbl_name.lower() == tbl_name else tbl_name

    if ctable and ('__inherits' not in tbl or not tbl['__inherits']):
        tbl['__inherits'] = 'BaseCache11' if cls_name.endswith('11') else 'BaseCache1n'

    res = "class {}({}, Model):\n".format(cls_name, tbl['__inherits'] if '__inherits' in tbl else 'Base')

    app_name = ''

    if '__meta' in tbl:
        res += '\n\tclass Meta:\n'
        res += '\t\ttable = "{}"\n'.format(tbl['__meta']['table_name'])
        res += '\t\tapp = "{}"\n'.format(tbl['__meta']['app'])

        if 'unique_together' in tbl['__meta']:
            res += '\t\tunique_together = {}\n'.format(tbl['__meta']['unique_together'])

        app_name = tbl['__meta']['app']

    if '__mk_cache_order' in tbl:
        res += f'\n\tmk_cache_order = {tbl["__mk_cache_order"]}\n'

    res += '\n'

    added_c_idx = set()

    types = {}

    schema_loc_dict = {'created': 'created', 'last_updated': 'last_updated'}

    service_loc_dict = {}

    mk_cache_rules = []

    for column_name in tbl:
        if not column_name.startswith('__'):
            value = tbl[column_name]
            orm_field = None
            if type(value) == str:
                raise NameError(f"NE MOZE OVAKO.. sredi da moze - {value}")
                orm_field = value
            elif type(value) == dict:
                if 'field' not in value:
                    continue

                if 'service' in value:
                    service_loc_dict[column_name] = value['service']

                if 'mk_cache' in value:
                    mkcr = {'column': column_name}
                    mkcr.update(value['mk_cache'])
                    mk_cache_rules.append(mkcr)

                orm_field = value['field']

                schema_loc = value['schema'] if 'schema' in value else None

                cname = column_name

                if 'fields.ForeignKeyField' in orm_field:
                    cname = column_name + '_id'

                if schema_loc:
                    schema_loc_dict[cname] = schema_loc
                else:
                    schema_loc_dict[cname] = cname

                if 'origin' in value:
                    if value['origin'] not in added_c_idx:
                        added_c_idx.add(value['origin'])

                        idx = value['origin']
                        if idx.startswith('id_'):
                            idx = 'idx_' + idx[3:]

                        parent_type = 'fields.UUIDField(null=True, index=True)'
                        if value["origin"] in parent_types:
                            parent_type = parent_types[value["origin"]]

                        have_index = 'index=True' in parent_type
                        if have_index:
                            res += f'''\t{idx} = {parent_type}\n'''
                        else:
                            parent_type = parent_type.rstrip(')') + ', index=True)'
                            res += f'''\t{idx} = {parent_type}\n'''

            res += f'\t{column_name} = {orm_field}\n'
            types[column_name] = orm_field

    if mk_cache_rules:
        if '__mk_cache_order' in tbl:
            mk_cache_order = tbl['__mk_cache_order']
            reordered = []
            for f in mk_cache_order:
                if f in mk_cache_rules:
                    reordered.append(m)
            if reordered:
                mk_cache_rules = reordered

    res += f'\n\tmk_cache_rules = {mk_cache_rules}\n\n'

    res += f'\n\t@staticmethod\n'
    res += f'\n\tdef schema_service_loc():\n'

    if not service_loc_dict:
        res += f'\t\treturn None\n'
    else:
        for s in service_loc_dict:
            res += f'\t\timport {'.'.join(service_loc_dict[s].split('.')[:-1])}\n'

        res += '\n\t\treturn {\n'
        for s in service_loc_dict:
            res += f'\t\t\t"{s}": {service_loc_dict[s]},\n'
        res += '}'

        res += '\n'

    res += f'\n\tschema_loc_dict={schema_loc_dict}\n'

    for c in ('cache11', 'cache1n'):
        if f'__{c}' in tbl:
            res += '\n' + gen_model(tbl[f'__{c}'], cls_name.capitalize() + f'C1{c[-1].upper()}', ctable=True, parent_class_name=cls_name, parent_types=types)

    return res


def gen_models(fname):
    with open(fname, 'rt') as f:
        model_definition = yaml.safe_load(f)

    res = f'''# THIS IS AN AUTO-GENERATED AND PROTECTED FILE. PLEASE USE
# THE gen_model.py SCRIPT TO GENERATE THIS FILE. DO NOT EDIT DIRECTLY
# AS IT CAN BE OVERWRITTEN. 
#
# FILE GENERATED ON: {datetime.datetime.now()}
    
import tortoise
from tortoise import fields
from base4.models.base import *
from tortoise.models import Model
from tortoise.fields import CASCADE, RESTRICT
import datetime

'''

    for tbl_name in list(model_definition.keys()):
        res += gen_model(model_definition[tbl_name], tbl_name)

    return res


def save(input_yaml, gen_fname):
    res = gen_models(input_yaml)

    os.system(f'rm -rf {gen_fname}')

    with open(gen_fname, 'wt') as f:
        f.write(res)

    os.system(f'isort {gen_fname} --line-length 160 ')

    os.system(f'black --target-version py312 --line-length 160 --skip-string-normalization {gen_fname}')

    os.system(f'chmod 444 {gen_fname}')

    # with open(gen_fname, 'rt') as f:
    #     print(f.read())


if __name__ == '__main__':
    current_file_path = os.path.abspath(os.path.dirname(__file__))

    save(current_file_path + '/../services/tickets/yaml_sources/ticket_model.yaml', '/tmp/generated_tickets_model.py')
