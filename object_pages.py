"""HubSpot-style object pages: Companies, Deals, Contact profile.

Imported by app.py and rendered when _active_page matches.
Uses companies / contacts / deals / associations / tags / property_definitions
tables created by SUPABASE_SETUP_PHASE4.sql.
"""
import html as html_mod
import streamlit as st
import pandas as pd
from datetime import date


def _esc(s):
    return html_mod.escape(str(s)) if s is not None else ""


def render_grouped_properties(sb, object_type, object_id, current_record, key_prefix=""):
    """Render a HubSpot-style grouped properties sidebar for an object.

    Reads property_definitions for the object_type, groups by group_name,
    renders editable fields. Saves back into the object's columns/properties JSONB.
    """
    try:
        r = sb.table("property_definitions").select("*").eq("object_type", object_type).order("display_order").execute()
        props = r.data or []
    except Exception:
        props = []

    # Group properties
    groups = {}
    for p in props:
        g = p.get("group_name") or "Other"
        groups.setdefault(g, []).append(p)

    edited = {}
    for group_name, plist in groups.items():
        with st.expander(group_name, expanded=True):
            for p in plist:
                name = p["name"]
                label = p.get("label") or name
                dtype = p.get("data_type", "text")
                # Read current value from row (column) or properties JSONB
                cur_val = current_record.get(name)
                if cur_val is None and current_record.get("properties"):
                    cur_val = current_record["properties"].get(name)

                widget_key = f"{key_prefix}_{object_type}_{object_id}_{name}"

                if dtype == "number":
                    try:
                        _nv = int(float(cur_val)) if cur_val not in (None, "") else 0
                    except (ValueError, TypeError):
                        _nv = 0
                    new_val = st.number_input(label, value=_nv, key=widget_key, step=1)
                elif dtype == "currency":
                    try:
                        _nv = float(cur_val) if cur_val not in (None, "") else 0.0
                    except (ValueError, TypeError):
                        _nv = 0.0
                    new_val = st.number_input(label, value=_nv, step=100.0, key=widget_key)
                elif dtype == "percent":
                    try:
                        _nv = float(cur_val) if cur_val not in (None, "") else 0.0
                    except (ValueError, TypeError):
                        _nv = 0.0
                    new_val = st.number_input(label, value=max(0.0, min(100.0, _nv)), step=0.1, min_value=0.0, max_value=100.0, key=widget_key)
                elif dtype == "boolean":
                    new_val = st.checkbox(label, value=bool(cur_val), key=widget_key)
                elif dtype == "date":
                    try:
                        dv = pd.to_datetime(cur_val).date() if cur_val else None
                    except Exception:
                        dv = None
                    new_val = st.date_input(label, value=dv, key=widget_key)
                    new_val = new_val.isoformat() if new_val else None
                elif dtype == "select":
                    opts = p.get("options") or []
                    if not opts:
                        new_val = st.text_input(label, value=str(cur_val or ""), key=widget_key)
                    else:
                        idx = opts.index(cur_val) if cur_val in opts else 0
                        new_val = st.selectbox(label, opts, index=idx, key=widget_key)
                elif dtype == "multi_select":
                    opts = p.get("options") or []
                    default = cur_val if isinstance(cur_val, list) else []
                    new_val = st.multiselect(label, opts, default=default, key=widget_key)
                else:
                    new_val = st.text_input(label, value=str(cur_val or ""), key=widget_key)

                edited[name] = new_val

    return edited


def render_tags(sb, object_type, object_id, key_prefix=""):
    """Display + manage tags for an object."""
    existing_tags = []
    try:
        # Get tag IDs for this object
        ot = sb.table("object_tags").select("tag_id").eq("object_type", object_type).eq("object_id", int(object_id)).execute()
        tag_ids = [r["tag_id"] for r in ot.data or [] if r.get("tag_id")]
        if tag_ids:
            t_rows = sb.table("tags").select("*").in_("id", tag_ids).execute()
            tag_map = {t["id"]: t for t in t_rows.data or []}
            for tid in tag_ids:
                t = tag_map.get(tid, {})
                existing_tags.append((tid, t.get("name"), t.get("color")))
    except Exception:
        pass

    pills = ""
    for tid, tname, tcolor in existing_tags:
        pills += f'<span style="display:inline-block;background:{tcolor or "#7c3aed"};color:#fff;font-size:11px;padding:2px 10px;border-radius:12px;margin:2px 4px 2px 0;">{_esc(tname)}</span>'
    if pills:
        st.markdown(pills, unsafe_allow_html=True)

    # Add tag
    try:
        all_tags = sb.table("tags").select("*").execute()
        tag_options = [t["name"] for t in all_tags.data or []]
    except Exception:
        tag_options = []

    tc1, tc2 = st.columns([3, 1])
    new_tag = tc1.text_input("Add tag", key=f"{key_prefix}_addtag", placeholder="Type tag name or pick existing", label_visibility="collapsed")
    if tc2.button("➕", key=f"{key_prefix}_addtag_btn"):
        if new_tag.strip():
            try:
                # Create tag if missing
                existing = sb.table("tags").select("id").eq("name", new_tag.strip()).execute()
                if existing.data:
                    tid = existing.data[0]["id"]
                else:
                    nt = sb.table("tags").insert({"name": new_tag.strip()}).execute()
                    tid = nt.data[0]["id"]
                sb.table("object_tags").upsert({
                    "object_type": object_type, "object_id": int(object_id), "tag_id": tid,
                }, on_conflict="object_type,object_id,tag_id").execute()
                st.rerun()
            except Exception as e:
                st.error(f"Tag add failed: {e}")


def render_quick_log(sb, object_type, object_id, key_prefix=""):
    """Quick-log a note / call / meeting / task on any object."""
    with st.expander("✍️ Quick log", expanded=False):
        a_type = st.selectbox("Type", ["note", "call", "meeting", "task"], key=f"{key_prefix}_qlog_type")
        a_subj = st.text_input("Subject", key=f"{key_prefix}_qlog_subj", placeholder="e.g. Demo booked / Left voicemail")
        a_body = st.text_area("Details", key=f"{key_prefix}_qlog_body", height=100)
        if st.button(f"Log {a_type}", key=f"{key_prefix}_qlog_btn", type="primary"):
            if not a_subj.strip() and not a_body.strip():
                st.error("Add subject or details")
            else:
                try:
                    from datetime import datetime as _dt
                    sb.table("activity").insert({
                        "object_type": object_type,
                        "object_id": int(object_id),
                        "type": a_type,
                        "subject": a_subj.strip() or a_type.title(),
                        "content": a_body.strip(),
                        "timestamp": _dt.now().isoformat(),
                    }).execute()
                    st.success(f"{a_type.title()} logged")
                    st.rerun()
                except Exception as e:
                    st.error(f"Log failed: {e}")


def render_associations(sb, object_type, object_id, on_click_navigate):
    """Show associated companies / contacts / deals as clickable cards.

    Queries BOTH directions: assocations FROM this object, and assocations TO this object.
    Dedupes by (other_type, other_id).
    """
    by_type = {"company": [], "contact": [], "deal": []}
    seen = set()
    try:
        # From this object → to other
        r = sb.table("associations").select("*").eq("from_object_type", object_type).eq("from_object_id", int(object_id)).execute()
        for a in r.data or []:
            t = a.get("to_object_type")
            tid = a.get("to_object_id")
            if t in by_type and (t, tid) not in seen:
                seen.add((t, tid))
                by_type[t].append({"to_object_type": t, "to_object_id": tid, "association_label": a.get("association_label")})
        # To this object ← from other
        r2 = sb.table("associations").select("*").eq("to_object_type", object_type).eq("to_object_id", int(object_id)).execute()
        for a in r2.data or []:
            t = a.get("from_object_type")
            tid = a.get("from_object_id")
            if t in by_type and (t, tid) not in seen:
                seen.add((t, tid))
                by_type[t].append({"to_object_type": t, "to_object_id": tid, "association_label": a.get("association_label")})
    except Exception:
        pass

    # Fetch the actual records in bulk
    company_ids = [a["to_object_id"] for a in by_type["company"]]
    contact_ids = [a["to_object_id"] for a in by_type["contact"]]
    deal_ids = [a["to_object_id"] for a in by_type["deal"]]

    company_map, contact_map, deal_map = {}, {}, {}
    if company_ids:
        cr = sb.table("companies").select("*").in_("id", company_ids).execute()
        company_map = {c["id"]: c for c in cr.data or []}
    if contact_ids:
        cr = sb.table("contacts").select("*").in_("id", contact_ids).execute()
        contact_map = {c["id"]: c for c in cr.data or []}
    if deal_ids:
        dr = sb.table("deals").select("*").in_("id", deal_ids).execute()
        deal_map = {d["id"]: d for d in dr.data or []}

    if company_map:
        st.markdown("##### 🏢 Companies")
        for cid, c in company_map.items():
            if st.button(f"🏢 {c.get('name')}", key=f"assoc_c_{object_type}_{object_id}_{cid}", use_container_width=True):
                on_click_navigate("company", cid)
    if contact_map:
        st.markdown("##### 👥 Contacts")
        for cid, c in contact_map.items():
            full_name = f"{c.get('first_name','') or ''} {c.get('last_name','') or ''}".strip() or c.get("email", "?")
            if st.button(f"👤 {full_name} · {c.get('email','')}", key=f"assoc_p_{object_type}_{object_id}_{cid}", use_container_width=True):
                on_click_navigate("contact", cid)
    if deal_map:
        st.markdown("##### 🤝 Deals")
        for did, d in deal_map.items():
            amount = f" · £{d.get('amount'):,.0f}" if d.get("amount") else ""
            stage = f" · {d.get('stage', '')}" if d.get("stage") else ""
            if st.button(f"🤝 {d.get('name')}{stage}{amount}", key=f"assoc_d_{object_type}_{object_id}_{did}", use_container_width=True):
                on_click_navigate("deal", did)


def navigate_to(obj_type, obj_id):
    """Set session_state so the right object profile renders next render."""
    st.session_state["view_object_type"] = obj_type
    st.session_state["view_object_id"] = int(obj_id)
    st.rerun()


def global_search(sb, q, limit=10):
    """Search across companies, contacts, deals. Returns list of (type, id, display)."""
    results = []
    if not q or len(q) < 2:
        return results
    # Sanitize for PostgREST or_ syntax (commas, parens have special meaning)
    safe = q.replace(",", " ").replace("(", "").replace(")", "")
    q_like = f"%{safe}%"
    try:
        cs = sb.table("companies").select("id, name, region").ilike("name", q_like).limit(limit).execute()
        for c in cs.data or []:
            results.append(("company", c["id"], f"🏢 {c['name']} · {c.get('region') or ''}"))
    except Exception:
        pass
    try:
        # Try matching email first, then name halves
        ps_email = sb.table("contacts").select("id, first_name, last_name, email").ilike("email", q_like).limit(limit).execute()
        seen_pids = set()
        for p in ps_email.data or []:
            full = f"{p.get('first_name','') or ''} {p.get('last_name','') or ''}".strip() or p.get("email", "?")
            results.append(("contact", p["id"], f"👤 {full} · {p.get('email','')}"))
            seen_pids.add(p["id"])
        ps_first = sb.table("contacts").select("id, first_name, last_name, email").ilike("first_name", q_like).limit(limit).execute()
        for p in ps_first.data or []:
            if p["id"] in seen_pids: continue
            full = f"{p.get('first_name','') or ''} {p.get('last_name','') or ''}".strip() or p.get("email", "?")
            results.append(("contact", p["id"], f"👤 {full} · {p.get('email','')}"))
            seen_pids.add(p["id"])
        ps_last = sb.table("contacts").select("id, first_name, last_name, email").ilike("last_name", q_like).limit(limit).execute()
        for p in ps_last.data or []:
            if p["id"] in seen_pids: continue
            full = f"{p.get('first_name','') or ''} {p.get('last_name','') or ''}".strip() or p.get("email", "?")
            results.append(("contact", p["id"], f"👤 {full} · {p.get('email','')}"))
    except Exception:
        pass
    try:
        ds = sb.table("deals").select("id, name, stage, amount").ilike("name", q_like).limit(limit).execute()
        for d in ds.data or []:
            results.append(("deal", d["id"], f"🤝 {d['name']} · {d.get('stage','')} · £{float(d.get('amount') or 0):,.0f}"))
    except Exception:
        pass
    return results


def render_companies_index(sb):
    """Companies list view — sortable table with quick stats."""
    h1, h2 = st.columns([4, 1])
    h1.markdown('<div style="font-size:22px;font-weight:700;color:#1a1a2e;margin-bottom:4px;">Companies</div>'
                '<div style="font-size:13px;color:#94a3b8;margin-bottom:20px;">All accounts. Click a row to open the company profile.</div>',
                unsafe_allow_html=True)
    if h2.button("➕ New company", key="co_new", type="primary", use_container_width=True):
        st.session_state["co_show_new_form"] = True

    if st.session_state.get("co_show_new_form"):
        with st.expander("Create new company", expanded=True):
            nc1, nc2 = st.columns(2)
            new_name = nc1.text_input("Company name *", key="co_new_name")
            new_region = nc2.text_input("Region / city", key="co_new_region")
            new_industry = nc1.text_input("Industry", key="co_new_industry")
            new_website = nc2.text_input("Website", key="co_new_website")
            new_locs = nc1.number_input("Number of locations", min_value=0, value=1, key="co_new_locs")
            new_terms = nc2.number_input("Number of terminals", min_value=0, value=0, key="co_new_terms")
            if st.button("Create", key="co_new_save", type="primary"):
                if not new_name.strip():
                    st.error("Name required")
                else:
                    try:
                        sb.table("companies").insert({
                            "name": new_name.strip(),
                            "region": new_region or None,
                            "industry": new_industry or None,
                            "website": new_website or None,
                            "num_locations": int(new_locs) or None,
                            "num_terminals": int(new_terms) or None,
                            "lifecycle_stage": "lead",
                        }).execute()
                        st.session_state["co_show_new_form"] = False
                        st.success(f"Created {new_name}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Create failed: {e}")
            if st.button("Cancel", key="co_new_cancel"):
                st.session_state["co_show_new_form"] = False
                st.rerun()

    # Filters
    f1, f2, f3 = st.columns([3, 2, 2])
    q = f1.text_input("Search", placeholder="🔍 Search by name…", key="co_search", label_visibility="collapsed")
    # Total count
    try:
        total_res = sb.table("companies").select("id", count="exact").execute()
        total = total_res.count
    except Exception:
        total = None

    # Load companies (paged)
    try:
        page_size = 50
        page = st.session_state.get("co_page", 0)
        offset = page * page_size

        query = sb.table("companies").select("*").order("name")
        if q and q.strip():
            safe = q.strip().replace("%", "").replace("_", "")
            query = query.ilike("name", f"%{safe}%")
        res = query.range(offset, offset + page_size - 1).execute()
        rows = res.data or []
    except Exception as e:
        st.error(f"Failed to load companies: {e}")
        return

    if total is not None:
        f2.caption(f"Showing {offset+1}–{offset+len(rows)} of {total:,} companies")

    if not rows:
        st.info("No companies yet. Run the migration script to import from existing leads.")
        return

    # Render rows
    for c in rows:
        with st.container():
            cc1, cc2, cc3, cc4, cc5 = st.columns([3, 2, 2, 1.5, 1.5])
            if cc1.button(f"🏢 {c['name']}", key=f"co_open_{c['id']}", use_container_width=True):
                navigate_to("company", c["id"])
            cc2.markdown(f'<div style="padding-top:8px;font-size:13px;color:#64748b;">{_esc(c.get("region") or "")}</div>', unsafe_allow_html=True)
            cc3.markdown(f'<div style="padding-top:8px;font-size:13px;color:#64748b;">{_esc(c.get("industry") or "")}</div>', unsafe_allow_html=True)
            cc4.markdown(f'<div style="padding-top:8px;font-size:13px;color:#1a1a2e;font-weight:600;">{c.get("num_locations") or "—"} loc</div>', unsafe_allow_html=True)
            cc5.markdown(f'<div style="padding-top:8px;font-size:13px;color:#1a1a2e;font-weight:600;">{c.get("num_terminals") or "—"} term</div>', unsafe_allow_html=True)
            st.markdown('<hr style="margin:4px 0;border:none;border-top:1px solid #f1f5f9;">', unsafe_allow_html=True)

    # Pagination
    pc1, pc2 = st.columns(2)
    if pc1.button("‹ Prev", disabled=(page == 0), key="co_prev"):
        st.session_state["co_page"] = max(0, page - 1)
        st.rerun()
    if pc2.button("Next ›", disabled=(len(rows) < page_size), key="co_next"):
        st.session_state["co_page"] = page + 1
        st.rerun()


def render_company_profile(sb, company_id):
    """HubSpot-style company profile with grouped properties + associations."""
    try:
        r = sb.table("companies").select("*").eq("id", int(company_id)).execute()
        if not r.data:
            st.error("Company not found")
            return
        company = r.data[0]
    except Exception as e:
        st.error(f"Load failed: {e}")
        return

    # Header
    h1, h2 = st.columns([4, 1])
    h1.markdown(f'''<div style="border-bottom:1px solid #e2e4e9;padding-bottom:12px;margin-bottom:20px;">
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:1px;">🏢 Company</div>
        <div style="font-size:28px;font-weight:700;color:#1a1a2e;">{_esc(company["name"])}</div>
        <div style="font-size:13px;color:#64748b;">{_esc(company.get("region") or "")} · {_esc(company.get("industry") or "")} · {company.get("num_locations") or "—"} locations · {company.get("num_terminals") or "—"} terminals</div>
    </div>''', unsafe_allow_html=True)
    if h2.button("← Back to Companies", key="co_back"):
        st.session_state["view_object_type"] = None
        st.session_state["view_object_id"] = None
        st.rerun()

    # 3-column HubSpot layout: timeline | main | sidebar
    main_col, side_col = st.columns([2, 1])

    with main_col:
        render_quick_log(sb, "company", company_id, key_prefix="co")
        # Tabs: activity / notes / emails / tasks
        tab = st.radio("View", ["📜 Activity", "📝 Notes", "🤝 Deals", "👥 Contacts"], horizontal=True, key="co_tab", label_visibility="collapsed")

        if tab == "📜 Activity":
            try:
                acts = sb.table("activity").select("*").eq("object_type", "company").eq("object_id", int(company_id)).order("timestamp", desc=True).limit(50).execute()
            except Exception:
                acts = type("X", (), {"data": []})()
            if not acts.data:
                st.info("No activity yet for this company.")
            else:
                for a in acts.data:
                    st.markdown(f'<div style="background:#fff;border:1px solid #e2e4e9;border-radius:8px;padding:12px;margin-bottom:8px;">'
                                f'<div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:1px;">{_esc(a.get("type"))} · {_esc(str(a.get("timestamp",""))[:16])}</div>'
                                f'<div style="font-size:14px;font-weight:600;color:#1a1a2e;margin:4px 0;">{_esc(a.get("subject") or "")}</div>'
                                f'<div style="font-size:13px;color:#64748b;white-space:pre-wrap;">{_esc((a.get("content") or "")[:300])}</div>'
                                f'</div>', unsafe_allow_html=True)

        elif tab == "📝 Notes":
            new_note = st.text_area("Add a note", key="co_new_note", placeholder="What just happened…")
            if st.button("Save note", key="co_save_note", type="primary"):
                if new_note.strip():
                    try:
                        sb.table("activity").insert({
                            "object_type": "company", "object_id": int(company_id),
                            "type": "note", "subject": "Note", "content": new_note.strip(),
                        }).execute()
                        st.success("Saved")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Save failed: {e}")

        elif tab == "🤝 Deals":
            render_associations(sb, "company", company_id, navigate_to)

        elif tab == "👥 Contacts":
            render_associations(sb, "company", company_id, navigate_to)

    with side_col:
        st.markdown("### Properties")
        edited = render_grouped_properties(sb, "company", company_id, company, key_prefix="co")
        if st.button("💾 Save changes", type="primary", key="co_save", use_container_width=True):
            try:
                # Split column updates vs properties JSONB
                col_set = {p["name"] for p in (sb.table("property_definitions").select("name").eq("object_type", "company").execute().data or [])}
                column_updates = {}
                jsonb_updates = dict(company.get("properties") or {})
                for k, v in edited.items():
                    # Try to set as column first (assume columns named same as prop name)
                    column_updates[k] = v if v not in ("", None) else None
                # Filter to only valid columns
                cols_in_table = ["name","domain","industry","region","address","phone","website","employee_count","annual_revenue","num_locations","num_terminals","pos_system","integrations","current_rates","lifecycle_stage","health_score","owner","source"]
                final_updates = {k: v for k, v in column_updates.items() if k in cols_in_table}
                # Rest go to JSONB
                for k, v in edited.items():
                    if k not in cols_in_table:
                        jsonb_updates[k] = v
                final_updates["properties"] = jsonb_updates
                from datetime import datetime as _dt
                final_updates["updated_at"] = _dt.now().isoformat()
                sb.table("companies").update(final_updates).eq("id", int(company_id)).execute()
                st.success("Saved")
                st.rerun()
            except Exception as e:
                st.error(f"Save failed: {e}")

        st.markdown("### Tags")
        render_tags(sb, "company", company_id, key_prefix="co")

        st.markdown("### Quick associations")
        render_associations(sb, "company", company_id, navigate_to)


def render_deals_kanban(sb, pipelines):
    """Kanban-style deal board reading from deals + associations."""
    st.markdown('<div style="font-size:22px;font-weight:700;color:#1a1a2e;margin-bottom:4px;">Deals Pipeline</div>'
                '<div style="font-size:13px;color:#94a3b8;margin-bottom:20px;">Drag-style kanban — deals grouped by stage.</div>',
                unsafe_allow_html=True)

    pipe_names = list(pipelines.keys()) if pipelines else ["Sales"]
    active_pipe = st.selectbox("Pipeline", pipe_names, key="dk_pipe")
    stages = pipelines.get(active_pipe, ["New", "Contacted", "Demo Booked", "Proposal", "Won", "Lost"])

    try:
        all_deals = sb.table("deals").select("*").eq("pipeline", active_pipe).limit(2000).execute()
        deals = all_deals.data or []
    except Exception as e:
        st.error(f"Load failed: {e}")
        return

    # Fetch company names per deal via associations
    deal_ids = [d["id"] for d in deals]
    deal_company_map = {}
    if deal_ids:
        try:
            assoc = sb.table("associations").select("*").in_("from_object_id", deal_ids).eq("from_object_type", "deal").eq("to_object_type", "company").execute()
            company_ids = [a["to_object_id"] for a in assoc.data or []]
            if company_ids:
                cos = sb.table("companies").select("id, name").in_("id", company_ids).execute()
                co_map = {c["id"]: c["name"] for c in cos.data or []}
                for a in assoc.data or []:
                    deal_company_map[a["from_object_id"]] = co_map.get(a["to_object_id"], "")
        except Exception:
            pass

    cols = st.columns(len(stages))
    for i, stage in enumerate(stages):
        stage_deals = [d for d in deals if d.get("stage") == stage]
        stage_total = sum(float(d.get("amount") or 0) for d in stage_deals)
        with cols[i]:
            st.markdown(f'<div style="background:#f8f9fb;border:1px solid #e2e4e9;border-radius:8px;padding:10px;margin-bottom:8px;">'
                        f'<div style="font-size:13px;font-weight:600;color:#1a1a2e;">{stage}</div>'
                        f'<div style="font-size:11px;color:#94a3b8;">{len(stage_deals)} deals · £{stage_total:,.0f}</div>'
                        f'</div>', unsafe_allow_html=True)
            for d in stage_deals[:15]:
                co_name = deal_company_map.get(d["id"], "")
                if st.button(f"{co_name or d['name']}\n£{float(d.get('amount') or 0):,.0f}", key=f"dk_{d['id']}", use_container_width=True):
                    navigate_to("deal", d["id"])


def render_deals_index(sb, pipelines=None):
    """Deals list / kanban view."""
    # View toggle
    view_mode = st.radio("View", ["📊 Kanban", "📋 List"], horizontal=True, key="deals_view", label_visibility="collapsed")
    if view_mode == "📊 Kanban":
        render_deals_kanban(sb, pipelines or {})
        return

    h1, h2 = st.columns([4, 1])
    h1.markdown('<div style="font-size:22px;font-weight:700;color:#1a1a2e;margin-bottom:4px;">Deals</div>'
                '<div style="font-size:13px;color:#94a3b8;margin-bottom:20px;">All opportunities in flight.</div>',
                unsafe_allow_html=True)
    if h2.button("➕ New deal", key="deal_new", type="primary", use_container_width=True):
        st.session_state["deal_show_new_form"] = True

    if st.session_state.get("deal_show_new_form"):
        with st.expander("Create new deal", expanded=True):
            dn1, dn2 = st.columns(2)
            nd_name = dn1.text_input("Deal name *", key="deal_new_name")
            nd_pipe = dn2.selectbox("Pipeline", ["Sales", "POS Customers", "Referral"], key="deal_new_pipe")
            nd_stage = dn1.text_input("Stage", value="New", key="deal_new_stage")
            nd_amount = dn2.number_input("Amount (£)", min_value=0.0, value=0.0, step=500.0, key="deal_new_amount")
            # Optional company link
            cos = sb.table("companies").select("id, name").limit(2000).execute()
            co_options = ["(none)"] + [f"{c['name']} (#{c['id']})" for c in cos.data or []]
            nd_co = st.selectbox("Link to company", co_options, key="deal_new_co")
            if st.button("Create deal", key="deal_new_save", type="primary"):
                if not nd_name.strip():
                    st.error("Name required")
                else:
                    try:
                        deal_payload = {
                            "name": nd_name.strip(),
                            "pipeline": nd_pipe,
                            "stage": nd_stage,
                            "amount": float(nd_amount) or None,
                        }
                        ins = sb.table("deals").insert(deal_payload).execute()
                        new_did = ins.data[0]["id"]
                        # Link to company if picked
                        if nd_co != "(none)":
                            co_id = int(nd_co.rsplit("#", 1)[1].rstrip(")"))
                            sb.table("associations").upsert({
                                "from_object_type": "deal", "from_object_id": new_did,
                                "to_object_type": "company", "to_object_id": co_id,
                                "association_label": "primary_company",
                            }, on_conflict="from_object_type,from_object_id,to_object_type,to_object_id,association_label").execute()
                        st.session_state["deal_show_new_form"] = False
                        st.success(f"Created {nd_name}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Create failed: {e}")
            if st.button("Cancel", key="deal_new_cancel"):
                st.session_state["deal_show_new_form"] = False
                st.rerun()
    try:
        res = sb.table("deals").select("*").order("created_at", desc=True).limit(100).execute()
        rows = res.data or []
    except Exception as e:
        st.error(f"Load failed: {e}")
        return

    if not rows:
        st.info("No deals yet. Run the migration to create from existing leads with non-New stages.")
        return

    for d in rows:
        with st.container():
            dc1, dc2, dc3, dc4 = st.columns([3, 2, 1.5, 1.5])
            if dc1.button(f"🤝 {d['name']}", key=f"deal_open_{d['id']}", use_container_width=True):
                navigate_to("deal", d["id"])
            dc2.markdown(f'<div style="padding-top:8px;font-size:13px;color:#64748b;">{_esc(d.get("stage") or "")}</div>', unsafe_allow_html=True)
            dc3.markdown(f'<div style="padding-top:8px;font-size:13px;color:#1a1a2e;font-weight:600;">£{float(d.get("amount") or 0):,.0f}</div>', unsafe_allow_html=True)
            dc4.markdown(f'<div style="padding-top:8px;font-size:12px;color:#94a3b8;">{_esc(str(d.get("close_date") or "—"))}</div>', unsafe_allow_html=True)
            st.markdown('<hr style="margin:4px 0;border:none;border-top:1px solid #f1f5f9;">', unsafe_allow_html=True)


def render_deal_profile(sb, deal_id):
    """HubSpot-style deal profile."""
    try:
        r = sb.table("deals").select("*").eq("id", int(deal_id)).execute()
        if not r.data:
            st.error("Deal not found")
            return
        deal = r.data[0]
    except Exception as e:
        st.error(f"Load failed: {e}")
        return

    h1, h2 = st.columns([4, 1])
    h1.markdown(f'''<div style="border-bottom:1px solid #e2e4e9;padding-bottom:12px;margin-bottom:20px;">
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:1px;">🤝 Deal</div>
        <div style="font-size:28px;font-weight:700;color:#1a1a2e;">{_esc(deal["name"])}</div>
        <div style="font-size:13px;color:#64748b;">{_esc(deal.get("stage") or "")} · £{float(deal.get("amount") or 0):,.0f} · {_esc(deal.get("pipeline") or "")}</div>
    </div>''', unsafe_allow_html=True)
    if h2.button("← Back to Deals", key="deal_back"):
        st.session_state["view_object_type"] = None
        st.session_state["view_object_id"] = None
        st.rerun()

    main_col, side_col = st.columns([2, 1])
    with main_col:
        render_quick_log(sb, "deal", deal_id, key_prefix="deal")
        st.markdown("### Activity")
        try:
            acts = sb.table("activity").select("*").eq("object_type", "deal").eq("object_id", int(deal_id)).order("timestamp", desc=True).limit(50).execute()
            for a in acts.data or []:
                st.markdown(f'**{a.get("type")}** · {a.get("timestamp")} — {a.get("subject", "")}')
        except Exception:
            pass

        st.markdown("### Notes")
        new_note = st.text_area("Add note", key="deal_new_note")
        if st.button("Save note", key="deal_save_note"):
            if new_note.strip():
                sb.table("activity").insert({"object_type": "deal", "object_id": int(deal_id), "type": "note", "subject": "Note", "content": new_note.strip()}).execute()
                st.rerun()

    with side_col:
        st.markdown("### Properties")
        edited = render_grouped_properties(sb, "deal", deal_id, deal, key_prefix="deal")
        if st.button("💾 Save", type="primary", key="deal_save", use_container_width=True):
            cols_in_table = ["name","pipeline","stage","amount","currency","probability","close_date","owner","num_terminals","rates_offered","contract_term_months","source"]
            final_updates = {k: v for k, v in edited.items() if k in cols_in_table and v not in ("", None)}
            jsonb_updates = dict(deal.get("properties") or {})
            for k, v in edited.items():
                if k not in cols_in_table:
                    jsonb_updates[k] = v
            final_updates["properties"] = jsonb_updates
            sb.table("deals").update(final_updates).eq("id", int(deal_id)).execute()
            st.success("Saved")
            st.rerun()

        st.markdown("### Associations")
        render_associations(sb, "deal", deal_id, navigate_to)

        st.markdown("### Tags")
        render_tags(sb, "deal", deal_id, key_prefix="deal")


def render_contact_profile_v2(sb, contact_id):
    """HubSpot-style contact profile with associations."""
    try:
        r = sb.table("contacts").select("*").eq("id", int(contact_id)).execute()
        if not r.data:
            st.error("Contact not found")
            return
        contact = r.data[0]
    except Exception as e:
        st.error(f"Load failed: {e}")
        return

    full = f"{contact.get('first_name','') or ''} {contact.get('last_name','') or ''}".strip() or contact.get("email", "?")

    h1, h2 = st.columns([4, 1])
    h1.markdown(f'''<div style="border-bottom:1px solid #e2e4e9;padding-bottom:12px;margin-bottom:20px;">
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:1px;">👤 Contact</div>
        <div style="font-size:28px;font-weight:700;color:#1a1a2e;">{_esc(full)}</div>
        <div style="font-size:13px;color:#64748b;">{_esc(contact.get("email") or "")} · {_esc(contact.get("phone") or "")} · {_esc(contact.get("title") or "")}</div>
    </div>''', unsafe_allow_html=True)
    if h2.button("← Back", key="ct_back"):
        st.session_state["view_object_type"] = None
        st.session_state["view_object_id"] = None
        st.rerun()

    main_col, side_col = st.columns([2, 1])
    with main_col:
        render_quick_log(sb, "contact", contact_id, key_prefix="ct")
        # Engagement summary at top
        try:
            ev = sb.table("email_events").select("event_type, occurred_at").eq("recipient_email", (contact.get("email") or "").lower()).execute()
            ev_counts = {}
            last_open = None
            for e in ev.data or []:
                et = e.get("event_type")
                ev_counts[et] = ev_counts.get(et, 0) + 1
                if et == "opened" and (last_open is None or e["occurred_at"] > last_open):
                    last_open = e["occurred_at"]
            if ev_counts:
                ec1, ec2, ec3, ec4, ec5 = st.columns(5)
                ec1.metric("📨 Delivered", ev_counts.get("delivered", 0))
                ec2.metric("👁 Opens", ev_counts.get("opened", 0))
                ec3.metric("🔗 Clicks", ev_counts.get("clicked", 0))
                ec4.metric("💬 Replies", ev_counts.get("replied", 0))
                ec5.metric("⚠️ Bounces", ev_counts.get("bounced", 0))
                if last_open:
                    st.caption(f"Last opened: {last_open[:16]}")
        except Exception:
            pass

        st.markdown("### Timeline")
        # Activity log entries
        timeline = []
        try:
            acts = sb.table("activity").select("*").or_(f"object_type.eq.contact,lead_id.eq.{contact.get('legacy_lead_id') or 0}").eq("object_id" if False else "object_type", "contact").execute()
            for a in acts.data or []:
                timeline.append({
                    "ts": a.get("timestamp", ""),
                    "icon": {"email": "✉️", "call": "📞", "note": "📝", "task": "✅", "meeting": "📅"}.get(a.get("type",""), "•"),
                    "label": f"{a.get('subject') or a.get('type','')} — {(a.get('content') or '')[:120]}",
                })
        except Exception:
            pass
        # Email events
        try:
            ev_t = sb.table("email_events").select("*").eq("recipient_email", (contact.get("email") or "").lower()).order("occurred_at", desc=True).limit(50).execute()
            for e in ev_t.data or []:
                icon = {"sent": "✉️", "delivered": "✅", "opened": "👁", "clicked": "🔗", "bounced": "⚠️", "replied": "💬"}.get(e.get("event_type",""), "•")
                timeline.append({
                    "ts": e.get("occurred_at", ""),
                    "icon": icon,
                    "label": f"Email {e.get('event_type','')} (via {e.get('source','')})",
                })
        except Exception:
            pass
        # Sort by timestamp desc
        timeline.sort(key=lambda x: x["ts"] or "", reverse=True)
        if not timeline:
            st.info("No activity yet.")
        else:
            for item in timeline[:50]:
                ts_short = str(item["ts"])[:16]
                st.markdown(f'<div style="font-size:13px;padding:6px 0;border-bottom:1px solid #f1f5f9;">'
                            f'<span style="color:#94a3b8;font-size:11px;">{ts_short}</span> &nbsp; '
                            f'<strong>{item["icon"]}</strong> {_esc(item["label"])}</div>',
                            unsafe_allow_html=True)

    with side_col:
        st.markdown("### Properties")
        edited = render_grouped_properties(sb, "contact", contact_id, contact, key_prefix="ct")
        if st.button("💾 Save", type="primary", key="ct_save", use_container_width=True):
            cols_in_table = ["first_name","last_name","email","phone","title","role","linkedin","lifecycle_stage","owner","do_not_email","bounced"]
            final_updates = {k: v for k, v in edited.items() if k in cols_in_table and v not in ("", None)}
            jsonb_updates = dict(contact.get("properties") or {})
            for k, v in edited.items():
                if k not in cols_in_table:
                    jsonb_updates[k] = v
            final_updates["properties"] = jsonb_updates
            sb.table("contacts").update(final_updates).eq("id", int(contact_id)).execute()
            st.success("Saved")
            st.rerun()

        st.markdown("### Associations")
        render_associations(sb, "contact", contact_id, navigate_to)

        # Sequence enrollments (via legacy lead_id matched to contact.email if migrated)
        try:
            # Lookup matching leads by email
            if contact.get("email"):
                legacy = sb.table("leads").select("id").ilike("email", contact["email"]).execute()
                legacy_ids = [r["id"] for r in legacy.data or []]
                if legacy_ids:
                    seq = sb.table("sequence_queue").select("*").in_("lead_id", [int(x) for x in legacy_ids]).execute()
                    if seq.data:
                        by_seq = {}
                        for r in seq.data:
                            by_seq.setdefault(r["sequence_name"], []).append(r)
                        st.markdown("### Sequences")
                        for sn, rows in by_seq.items():
                            done = sum(1 for r in rows if r["status"] == "done")
                            pending = sum(1 for r in rows if r["status"] == "pending")
                            st.markdown(f"**{sn}** — ✅ {done} sent · ⏳ {pending} pending")
        except Exception:
            pass

        st.markdown("### Tags")
        render_tags(sb, "contact", contact_id, key_prefix="ct")
