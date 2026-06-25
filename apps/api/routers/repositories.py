from fastapi import APIRouter

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.get("")
async def list_repositories():
    # Placeholder — implemented fully in Step 5 with auth
    return {"repositories": [], "message": "Auth required — coming in Step 5"}