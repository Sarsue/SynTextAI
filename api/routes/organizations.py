"""Organization (tenant) endpoints.

A person can belong to more than one organization: staff at one company and the
owner of their own. Rather than a persistent switcher, the app asks which
organization they are entering when there is more than one, so the tenant
boundary is explicit rather than a dropdown people misread.
"""
from typing import Dict, List, Optional
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, status
from pydantic import BaseModel, EmailStr

from ..core.utils import get_user_id
from ..core.limits import resolve_entitlement
from ..core.seats import seat_summary
from ..services.email_service import send_workspace_invite, EmailNotConfigured, app_url
from api.repositories.repository_manager import RepositoryManager

logger = logging.getLogger(__name__)

organizations_router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])


def get_store(request: Request):
    return request.app.state.store


async def authenticate_user(
    authorization: str = Header(None), store: RepositoryManager = Depends(get_store)
):
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    success, user_info = get_user_id(authorization)
    if not success:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    user_id = await store.user_repo.get_user_id_from_email(user_info["email"])
    if not user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return {"user_id": user_id, "user_info": user_info}


class OrganizationSummary(BaseModel):
    organization_id: int
    name: str
    role: str
    entitled: bool
    member_count: int


class MemberSummary(BaseModel):
    user_id: int
    email: Optional[str] = None
    role: str


@organizations_router.get("")
async def list_my_organizations(
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Organizations this user belongs to, for the sign-in chooser.

    A single entry means the client should select it silently and go straight to
    chat; the chooser only appears when there is a genuine choice to make.

    An empty list is normal now that signup creates only the person. It means one
    of two very different things, so `has_pending_invite` distinguishes them: a
    new customer who should start a trial, or somebody who has been invited and
    should follow their link rather than be asked to pay for anything.
    """
    user_id = user_data["user_id"]
    email = (user_data["user_info"].get("email") or "")
    memberships = await store.org_repo.get_memberships(user_id)

    items = []
    for m in memberships:
        org_id = m["organization_id"]
        org_status = await store.org_repo.get_subscription_status(org_id)
        items.append(
            OrganizationSummary(
                organization_id=org_id,
                name=m["name"],
                role=m["role"],
                entitled=(org_status or "none").lower() in {"active", "trialing"},
                member_count=await store.org_repo.count_members(org_id),
            )
        )
    return {"items": items}


# Deliberately no POST /organizations.
#
# Creating an organization and paying for it are the same act: a tenant with no
# subscription is entitled to nothing, so a bare "create organization" call
# produces an account that is locked out of the product the moment it exists.
# Organizations are therefore born in the signup funnel, where the trial starts
# alongside them. Sign in and invites never create one.


class RenameOrganizationRequest(BaseModel):
    name: str


@organizations_router.patch("/{organization_id}")
async def rename_organization(
    body: RenameOrganizationRequest,
    organization_id: int = Path(...),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Rename an organization. Owners and admins only.

    Organizations are created with a name derived from the signup email, which
    reads as "drsmith's Organization". That name is what members see in the
    chooser and what invite emails announce, so it should be the company's
    actual name. Onboarding asks for it and calls this.
    """
    user_id = user_data["user_id"]
    role = await store.org_repo.get_role(organization_id, user_id)
    if role not in ("owner", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an owner or admin can rename the organization.",
        )

    name = (body.name or "").strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Organization name is required."
        )

    if not await store.org_repo.rename_organization(organization_id, name):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not rename organization.",
        )
    return {"organization_id": organization_id, "name": name}


@organizations_router.get("/{organization_id}/context")
async def organization_context(
    organization_id: int = Path(...),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """What this user may do inside one organization.

    Everything the UI needs to render the right view is resolved here against a
    single tenant, so entitlement and role can never be blended across two
    organizations the user happens to belong to.
    """
    user_id = user_data["user_id"]
    role = await store.org_repo.get_role(organization_id, user_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization.",
        )

    org_status = await store.org_repo.get_subscription_status(organization_id)
    entitled = (org_status or "none").lower() in {"active", "trialing"}
    is_admin = role in ("owner", "admin")

    memberships = await store.org_repo.get_memberships(user_id)
    name = next(
        (m["name"] for m in memberships if m["organization_id"] == organization_id), None
    )

    return {
        "organization_id": organization_id,
        "name": name,
        "role": role,
        "subscription_status": org_status,
        "entitled": entitled,
        # Billing belongs to the owner. A member never sees it, and is never
        # asked to fix somebody else's lapsed plan.
        "can_manage_billing": role == "owner",
        "can_manage_members": is_admin,
        "can_manage_documents": is_admin,
        "can_rename_organization": is_admin,
        "seats_used": await store.org_repo.count_members(organization_id),
        "seat_limit": await store.org_repo.get_seat_limit(organization_id),
    }


@organizations_router.get("/{organization_id}/members", response_model=Dict[str, List[MemberSummary]])
async def list_organization_members(
    organization_id: int = Path(...),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Members of an organization. Any member may see who is on the team."""
    user_id = user_data["user_id"]
    role = await store.org_repo.get_role(organization_id, user_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization.",
        )
    members = await store.org_repo.list_members(organization_id)
    return {"items": [MemberSummary(**{k: m[k] for k in ("user_id", "email", "role")}) for m in members]}


class OrganizationInviteRequest(BaseModel):
    email: EmailStr


@organizations_router.post("/{organization_id}/invites", status_code=status.HTTP_201_CREATED)
async def invite_to_organization(
    organization_id: int = Path(...),
    body: OrganizationInviteRequest = ...,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Invite somebody to the whole organization, not to one workspace.

    They see every workspace in the tenant, including ones created after they
    joined. Use the workspace invite instead when access should be limited to a
    single workspace: a practice that keeps payroll separate from the front-desk
    handbook wants that one, not this.

    Owners and admins only, since this hands out the widest access there is
    short of administering the tenant.
    """
    user_id = user_data["user_id"]
    role = await store.org_repo.get_role(organization_id, user_id)
    if role not in ("owner", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an owner or admin can invite people to the organization.",
        )

    token = await store.workspace_repo.create_invite(
        body.email, organization_id=organization_id
    )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create invite",
        )

    organizations = await store.org_repo.get_memberships(user_id)
    org_name = next(
        (o["name"] for o in organizations if o["organization_id"] == organization_id),
        "your team",
    )
    inviter_name = user_data["user_info"].get("name") or user_data["user_info"].get("email", "Your team")

    # Seats are charged per member, so tell the inviter what this one costs
    # rather than letting it surface on the next invoice.
    seats = await seat_summary(store, organization_id)

    try:
        invite_url = send_workspace_invite(
            to_email=body.email,
            workspace_name=org_name,
            token=token,
            inviter_name=inviter_name,
        )
        return {
            "message": f"Invite sent to {body.email}",
            "email_sent": True,
            "invite_url": invite_url,
            "next_seat_cents": seats.get("next_seat_cents", 0),
        }
    except EmailNotConfigured as e:
        logger.warning(f"Invite created for {body.email} but email is not configured: {e}")
    except Exception as e:
        logger.error(f"Invite created for {body.email} but sending failed: {e}", exc_info=True)

    return {
        "message": f"Invite created for {body.email}, but the email could not be sent. Share this link with them.",
        "email_sent": False,
        "invite_url": f"{app_url()}/#/invite/{token}",
        "next_seat_cents": seats.get("next_seat_cents", 0),
    }
