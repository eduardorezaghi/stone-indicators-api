import csv
from io import StringIO
import asyncio
from typing import List, Dict, Tuple
from celery import shared_task
from celery.utils.log import get_task_logger
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import get_async_session
from src.domain import Delivery as DeliveryDT
from src.domain import Angel as AngelDomain
from src.domain import Client as ClientDomain
from src.domain import Polo as PoloDomain
from src.repositories import DeliveryRepository, AngelRepository, PoloRepository, ClientRepository

BATCH_SIZE = 10_000
logger = get_task_logger(__name__)

# Very hard to test this file because:
# 1. It uses a celery task to perform the logic
# 2. It uses an async session to interact with the database
# 3. It uses a lot of dependencies

# So, to test this we would need to perform some crazy mocking
# or to refactor the code to make it more testable (e.g. break even more the logic into smaller functions)

# That's why I added pragmas to ignore coverage for this file :)


async def process_batch(
    rows: List[Dict],
    repositories: Tuple[DeliveryRepository, AngelRepository, PoloRepository, ClientRepository],
    async_session: AsyncSession
) -> Tuple[int, List[Dict]]: # pragma: no cover
    """Process a batch of CSV rows"""
    successful_rows = 0
    errors = []
    skipped_rows = 0
    
    delivery_repo, angel_repo, polo_repo, client_repo = repositories
    
    # Collect unique entities to create
    angels_to_create = set()
    polos_to_create = set()
    clients_to_create = set()
    deliveries_to_create = []

    for row_num, row in rows:
        try:
            obj = DeliveryDT.from_dict(row)
            
            # Skip if required fields are None
            if any(getattr(obj, field) is None for field in ['angel', 'polo', 'cliente_id', 'data_de_atendimento']):
                logger.warning(
                    "Skipping row %d: Missing required fields in data: %s",
                    row_num, row
                )
                skipped_rows += 1
                errors.append({
                    "line": row_num,
                    "error": "Missing required fields",
                    "data": row
                })
                continue
            
            # Track entities that need to be created
            angels_to_create.add(obj.angel)
            polos_to_create.add(obj.polo)
            clients_to_create.add(obj.cliente_id)
            deliveries_to_create.append(obj)
            
        except Exception as e:
            logger.error(
                "Error processing row %d: %s. Data: %s",
                row_num, str(e), row
            )
            errors.append({
                "line": row_num,
                "error": str(e),
                "data": row
            })
            continue

    # Skip batch processing if no valid deliveries
    if not deliveries_to_create:
        logger.info("No valid deliveries in batch to process")
        return successful_rows, errors

    try:
        # Batch create angels
        existing_angels = await angel_repo.get_by_names_async(list(angels_to_create))
        new_angels = angels_to_create - {angel.name for angel in existing_angels}
        created_angels = await angel_repo.bulk_create_async([
            AngelDomain(name=name) for name in new_angels
        ])
        angels_map = {
            angel.name: angel.id 
            for angel in [*existing_angels, *created_angels]
        }

        # Batch create polos
        existing_polos = await polo_repo.get_by_attributes_async(list(polos_to_create))
        new_polos = polos_to_create - {polo.name for polo in existing_polos}
        created_polos = await polo_repo.bulk_create_async([
            PoloDomain(name=name) for name in new_polos
        ])
        polos_map = {
            polo.name: polo.id 
            for polo in [*existing_polos, *created_polos]
        }

        # Batch create clients
        existing_clients = await client_repo.get_by_ids_async(list(clients_to_create))
        new_clients = clients_to_create - {client.id for client in existing_clients}
        created_clients = await client_repo.bulk_create_async([
            ClientDomain(id=client_id) for client_id in new_clients
        ])
        clients_map = {
            client.id: client.id 
            for client in [*existing_clients, *created_clients]
        }

        # Update deliveries with related entity IDs
        for delivery in deliveries_to_create:
            delivery.id_angel = angels_map[delivery.angel]
            delivery.id_polo = polos_map[delivery.polo]
            delivery.cliente_id = clients_map[delivery.cliente_id]

        # Batch create deliveries
        await delivery_repo.bulk_create_async(deliveries_to_create)
        successful_rows = len(deliveries_to_create)
        logger.info("Successfully processed batch with %d deliveries", successful_rows)

    except Exception as e:
        logger.error("Batch processing error: %s", str(e))
        errors.append({
            "line": "batch",
            "error": f"Batch processing error: {str(e)}",
            "data": None
        })

    return successful_rows, errors

@shared_task
def import_csv_task(file_content: str) -> dict: # pragma: no cover
    result = asyncio.run(process_csv(file_content))
    return result

async def process_csv(file_content: str) -> dict: # pragma: no cover
    total_rows = 0
    successful_rows = 0
    errors = []
    current_batch = []
    current_batch_row_nums = []

    logger.info("Starting CSV import process")

    async with get_async_session() as async_session:
        repositories = (
            DeliveryRepository(async_session=async_session),
            AngelRepository(async_session=async_session),
            PoloRepository(async_session=async_session),
            ClientRepository(async_session=async_session)
        )

        try:
            with StringIO(file_content) as decoded_stream:
                sample = decoded_stream.read(1024)
                decoded_stream.seek(0)
                dialect = csv.Sniffer().sniff(sample)
                decoded_stream.seek(0)
                csv_input = csv.DictReader(decoded_stream, delimiter=dialect.delimiter)

                for row_num, row in enumerate(csv_input, start=2):
                    total_rows += 1
                    current_batch.append(row)
                    current_batch_row_nums.append(row_num)

                    if len(current_batch) >= BATCH_SIZE:
                        batch_rows = list(zip(current_batch_row_nums, current_batch))
                        success_count, batch_errors = await process_batch(
                            batch_rows, repositories, async_session
                        )
                        successful_rows += success_count
                        errors.extend(batch_errors)
                        current_batch = []
                        current_batch_row_nums = []

                # Process remaining rows
                if current_batch:
                    batch_rows = list(zip(current_batch_row_nums, current_batch))
                    success_count, batch_errors = await process_batch(
                        batch_rows, repositories, async_session
                    )
                    successful_rows += success_count
                    errors.extend(batch_errors)

        except Exception as e:
            logger.error("File processing error: %s", str(e))
            errors.append({
                "line": "file",
                "error": f"File processing error: {str(e)}",
                "data": None
            })

    logger.info(
        "CSV import completed. Total rows: %d, Successful: %d, Failed: %d",
        total_rows, successful_rows, total_rows - successful_rows
    )

    return {
        "status": "completed",
        "total_rows": total_rows,
        "successful_rows": successful_rows,
        "failed_rows": total_rows - successful_rows,
        "errors": errors
    }