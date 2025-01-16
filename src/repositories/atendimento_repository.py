from sqlalchemy import select

from src.database import default_db as db
from src.domain import Delivery as DeliveryDomain
from src.models import Angel, Client, Delivery, Polo
from src.repositories.base import BaseRepository


class DeliveryRepository(BaseRepository[Delivery]):
    available_order_by = [
        "id",
        "created_at",
        "updated_at",
        "id_cliente",
        "angel",
        "polo",
        "data_limite",
        "data_de_atendimento",
    ]

    async def get_by_id(self, id: int) -> Delivery | None:
        pass

    async def get_paginated(
        self, page: int, per_page: int, order_by_param: str
    ) -> list[Delivery]:
        if order_by_param not in self.available_order_by:
            raise ValueError(f"order_by_param must be one of {self.available_order_by}")

        query = (
            select(Delivery)
            .join(Client, Delivery.cliente_id == Client.id)
            .join(Angel, Delivery.angel_id == Angel.id)
            .join(Polo, Delivery.polo_id == Polo.id)
            .order_by(order_by_param)
            .filter(Delivery.deleted_at.is_(None))
        )
        paginated_query = db.paginate(
            query, page=page, per_page=per_page, error_out=False
        )

        return paginated_query.items

    async def create(self, data: DeliveryDomain) -> Delivery:
        dict_data = data.to_dict()
        entity = Delivery(**dict_data)

        with db.session() as session:
            session.add(entity)
            session.commit()
            session.refresh(entity)

        return entity

    async def create_many(self, data: list[DeliveryDomain]) -> list[Delivery]:
        entities = []
        with db.session() as session:
            for item in data:
                entity = Delivery(**item.to_dict())
                entities.append(entity)

            session.add_all(entities)

        return entities

    async def update(self, data: DeliveryDomain) -> Delivery | None:
        pass

    async def delete(self, id: int) -> bool:
        pass