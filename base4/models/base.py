import random
import string
import uuid

from fastapi import HTTPException
from tortoise import fields


class BaseCache11:
    id = fields.UUIDField(pk=True)


class BaseCache1n(BaseCache11):
    language = fields.CharField(2, null=False, index=True)


class Nothing:
    ...

class BaseNoTenant:
    """
    A base model class that includes common fields for all models.
    """

    id = fields.UUIDField(pk=True)
    created = fields.DatetimeField(auto_now_add=True)
    created_by = fields.UUIDField(null=True)
    last_updated = fields.DatetimeField(
        null=True,
       # auto_now=True
    )  # auto_now=True)
    last_updated_by = fields.UUIDField(null=True)
    validated = fields.DatetimeField(null=True)

    is_deleted = fields.BooleanField(default=False, null=False)
    is_valid = fields.BooleanField(default=False, null=False)
    deleted_by = fields.UUIDField(null=True)
    deleted = fields.DatetimeField(null=True)

    @classmethod
    async def gen_unique_id(cls, prefix='?', alphabet='WERTYUPASFGHJKLZXCVNM2345679', total_length=10, max_attempts=10):

        for attempt in range(max_attempts):
            unique_id = prefix + ''.join(random.choice(alphabet) for _ in range(total_length - len(prefix)))
            if await cls.filter(unique_id=unique_id).count() == 0:
                return unique_id

        raise HTTPException(
            status_code=500,
            detail={
                "code": "INTERNAL_SERVER_ERROR",
                "debug": f'Failed to generate unique id in {max_attempts} attempts',
                "message": "Failed to generate unique id",
            },
        )

    # @classmethod
    # async def gen_random_id(cls):
    #     def generate_random_id(n):
    #         characters = string.ascii_letters.upper() + string.digits * 3
    #         return ''.join(random.choice(characters) for _ in range(n))
    #
    #     attempt = 0
    #     while attempt < 10:
    #         random_id = generate_random_id(12)
    #         if not await cls.filter(unique_id=random_id).exists():
    #             return random_id
    #         attempt += 1
    #
    #     raise Exception("Could not generate unique random id")

    def __init__(self, logged_user_id: uuid.UUID, **kwargs):

        super().__init__(**kwargs)

        self.created_by = logged_user_id
        self.last_updated_by = logged_user_id

    async def save(self, *args, **kwargs):
        if hasattr(self, 'id_unique') and not self.id_unique:
            self.id_unique = await self.gen_random_id()

        await super().save(*args, **kwargs)


class Base(BaseNoTenant):
    id_tenant = fields.UUIDField(null=True, index=True)
