"""WebSocket real-time updates (Sprint 26)."""
from uuid import UUID

from fastapi import APIRouter, WebSocketDisconnect, WebSocket

router = APIRouter(
    prefix="",
    tags=["WebSocket"],
)


class ConnectionManager:
    """Manage active WebSocket connections."""

    def __init__(self):
        self.active_connections: dict[UUID, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, company_id: UUID):
        """Register new connection."""
        await websocket.accept()
        if company_id not in self.active_connections:
            self.active_connections[company_id] = []
        self.active_connections[company_id].append(websocket)

    async def disconnect(self, websocket: WebSocket, company_id: UUID):
        """Remove connection."""
        if company_id in self.active_connections:
            self.active_connections[company_id].remove(websocket)

    async def broadcast(self, company_id: UUID, message: dict):
        """Send message to all clients of a company."""
        if company_id in self.active_connections:
            for connection in self.active_connections[company_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    pass


manager = ConnectionManager()


@router.websocket("/ws/companies/{company_id}/live")
async def websocket_endpoint(
    websocket: WebSocket,
    company_id: UUID,
):
    """
    WebSocket: real-time updates on transactions & payments.
    Send {"type": "refresh"} to trigger dashboard update.
    """
    await manager.connect(websocket, company_id)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "refresh":
                # Broadcast refresh signal to all connected clients
                await manager.broadcast(
                    company_id,
                    {"type": "refresh", "message": "Dashboard updated"},
                )
    except WebSocketDisconnect:
        await manager.disconnect(websocket, company_id)
    except Exception as e:
        await manager.disconnect(websocket, company_id)
