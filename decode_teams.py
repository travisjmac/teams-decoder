#!/usr/bin/env python3

import json
import sys
import time
import os
import argparse
import xml.etree.ElementTree as ET
import html2text


def parse_args():
    global args

    myname = None
    try:
        import pwd
        myname = pwd.getpwuid(os.getuid()).pw_name
    except:
        pass

    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument(      "--debug",     action="store_true",     help="add some debug info to output")
    parser.add_argument("-o", "--directory", default="conversations", help="name of output directory")
    parser.add_argument("-n", "--name",      default=myname,          help="Full username (optional)")
    parser.add_argument("json",                                       help="teams.json source file")

    args = parser.parse_args()


mids = {}


def strip_worker_prefix(uid):
    if uid is not None and uid.startswith("worker/"):
        return uid[7:]
    return uid


def member_display(person):
    pid = person["id"]
    if "nameHint" in person and person["nameHint"] is not None:
        nh = person["nameHint"]
        if "displayName" in nh and nh["displayName"]:
            return pid, nh["displayName"]
    return pid, resolve(pid)


def format_sender_token(guid, display_name):
    if guid and display_name and display_name != guid:
        return f"{guid}|{display_name}"
    if guid:
        return guid
    return display_name or "Unknown"


def sender_from_message(msg):
    guid = strip_worker_prefix(msg.get("from"))
    if not guid:
        guid = strip_worker_prefix(msg.get("creator"))
    name = msg.get("imDisplayName")
    if not name and guid:
        name = resolve(guid)
    if not name:
        name = "Unknown"
    return format_sender_token(guid, name)


def rname(obj, namekey, idkey):
    if namekey in obj and obj[namekey] is not None and obj[namekey] != "":
        return obj[namekey]
    uid = obj[idkey]
    return resolve(uid)


def resolve(uid):
    if uid in mids:
#        return mids[uid]+f" ({uid})"
        return mids[uid]
    return uid


def is_real_display_name(uid, display_name):
    if not display_name or not str(display_name).strip():
        return False
    display_name = str(display_name).strip()
    if uid and display_name == uid:
        return False
    if display_name.startswith("8:orgid:") or display_name.startswith("19:"):
        return False
    return True


def record_mid(uid, display_name):
    uid = strip_worker_prefix(uid)
    if not uid or not is_real_display_name(uid, display_name):
        return
    mids[uid] = str(display_name).strip()


def message_im_display_name(msg):
    name = msg.get("imDisplayName")
    if not name:
        name = msg.get("imdisplayname")
    return name


def enrich_mids_from_message(msg):
    guid = strip_worker_prefix(msg.get("from"))
    if not guid:
        guid = strip_worker_prefix(msg.get("creator"))
    record_mid(guid, message_im_display_name(msg))


def enrich_mids_from_conversation(conv_value):
    for member in conv_value.get("members") or []:
        if not isinstance(member, dict):
            continue
        if "nameHint" not in member:
            continue
        nh = member["nameHint"]
        if nh is None:
            continue
        record_mid(member["id"], nh.get("displayName"))

    last_msg = conv_value.get("lastMessage")
    if isinstance(last_msg, dict):
        record_mid(last_msg.get("from"), message_im_display_name(last_msg))

    chat_title = conv_value.get("chatTitle")
    if not isinstance(chat_title, dict):
        return

    for info in chat_title.get("avatarUsersInfo") or []:
        uid = info.get("mri") or info.get("id")
        record_mid(uid, info.get("displayName"))

    members = conv_value.get("members") or []
    member_ids = []
    for m in members:
        if isinstance(m, dict) and m.get("id"):
            member_ids.append(m["id"])
    if conv_value.get("type") != "Chat" or len(member_ids) != 2:
        return

    short_title = chat_title.get("shortTitle")
    if is_real_display_name(None, short_title):
        self_id = None
        for mid in member_ids:
            if mid in mids and args.name and mids[mid] == args.name:
                self_id = mid
                break
        if self_id is not None and short_title != args.name:
            for mid in member_ids:
                if mid != self_id:
                    record_mid(mid, short_title)
                    break

    long_title = chat_title.get("longTitle")
    if not long_title:
        return
    title_names = [n.strip() for n in long_title.split(",") if n.strip()]
    if len(title_names) != len(member_ids):
        return
    unmatched_ids = [mid for mid in member_ids if not is_real_display_name(mid, resolve(mid))]
    unmatched_names = [
        n for n in title_names if n not in mids.values() and is_real_display_name(None, n)
    ]
    if len(unmatched_ids) == 1 and len(unmatched_names) == 1:
        record_mid(unmatched_ids[0], unmatched_names[0])


def existing_export_same_conversation(path, conv):
    """True if path is an export for this Teams conversation id (re-decode overwrite)."""
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8") as handle:
            for _ in range(12):
                line = handle.readline()
                if not line:
                    break
                line = line.strip()
                if line.startswith("ConversationId:"):
                    return line.split(":", 1)[1].strip() == conv
                if line.startswith("Id:"):
                    legacy = line.split(":", 1)[1].strip()
                    return legacy.startswith(conv + "[") or legacy == conv
    except OSError:
        pass
    return False


def find_export_path_for_conversation(outdir, conv):
    """Return path to an existing export in outdir for this conversation id, if any."""
    try:
        names = os.listdir(outdir)
    except OSError:
        return None
    for name in names:
        if not name.endswith(".txt"):
            continue
        path = os.path.join(outdir, name)
        if existing_export_same_conversation(path, conv):
            return path
    return None


def remove_other_exports_same_conversation(outdir, conv, keep_path):
    """Delete other exports in outdir whose header matches this conversation id."""
    keep_abs = os.path.normcase(os.path.abspath(keep_path))
    try:
        names = os.listdir(outdir)
    except OSError:
        return
    for name in names:
        if not name.endswith(".txt"):
            continue
        path = os.path.join(outdir, name)
        if os.path.normcase(os.path.abspath(path)) == keep_abs:
            continue
        if existing_export_same_conversation(path, conv):
            try:
                os.remove(path)
            except OSError:
                pass


def run():
    outdir = args.directory

    if not os.path.isdir(outdir):
        os.mkdir(outdir)

    with open(args.json, 'r', encoding='utf-8') as data_file:
        json_data = data_file.read()

    data = json.loads(json_data)

    messages = {}
    conversations = {}
    stats = {}
    global mids

    h2t = html2text.HTML2Text()
    h2t.images_to_alt = True
    h2t.body_width = 0

    # Filter conversations & reference by conversationId
    for item in data:
        if item["store"] == "replychains":
            mm = item["value"]["messageMap"]
            for k in mm.keys():
                msg = mm[k]

                mid = msg["conversationId"]
                if mid in messages:
                    messages[mid].append(msg)
                else:
                    messages[mid] = [msg]

        elif item["store"] == "conversations":
            cid = item["value"]["id"]
            enrich_mids_from_conversation(item["value"])
            conversations[cid] = item["value"]

    for msg_list in messages.values():
        for msg in msg_list:
            enrich_mids_from_message(msg)

    for conv in messages:
        if conv in conversations:
            typ = conversations[conv]["type"]
        else:
            typ = "Unknown"
            print("Unknown conversation", cid, file=sys.stderr)

        title = None
        members = []
        topic = None

        if True:  # remove some stuff for debugging
            del conversations[conv]["lastMessage"]
            if "properties" in conversations[conv]:
                if "quickReplyAugmentation" in conversations[conv]["properties"]:
                    del conversations[conv]["properties"]["quickReplyAugmentation"]

        if typ == "Meeting":
            topic = conversations[conv]["threadProperties"]["topic"]
            title = f"{topic}"

            members = []
            for person in conversations[conv]["members"]:
                members.append(member_display(person))

        elif typ == "Space":
            topic = conversations[conv]["threadProperties"]["spaceThreadTopic"]
            title = f"{topic}"

        elif typ == "Topic":
            topic = conversations[conv]["threadProperties"]["topicThreadTopic"]
            title = f"{topic}"

        elif typ == "Chat":
            title = f"{typ}"
            members = []
            for person in conversations[conv]["members"]:
                members.append(member_display(person))

        elif typ == "Thread":  # Call log?
            pass

        elif typ == "Conversation":  # Notifications?
            pass

        else:
            print("Unknown conversation type", typ, file=sys.stderr)

        if typ in stats:
            stats[typ] += 1
        else:
            stats[typ] = 1

        # Construct filename
        fname = conv
        if topic is not None:
            fname = topic
        if typ == "Chat" and len(members) > 0:
            tmem = [name for _pid, name in members]
            # Remove myself from the filename, if present
            if args.name is not None and args.name in tmem:
                tmem.remove(args.name)
            fname = ",".join(tmem if tmem else [name for _pid, name in members])

        fname = typ+"_"+fname
        fname = fname.replace(" ", "_")
        fname = fname.replace('/', '')
        # Sanitize characters that are invalid in Windows filenames (and
        # awkward elsewhere), e.g. ':' from "8:orgid:..." ids.
        for bad_char in '<>:"\\|?*':
            fname = fname.replace(bad_char, '')
        # Keep filenames well within filesystem limits (large group chats can
        # produce very long comma-separated member lists).
        if len(fname) > 150:
            fname = fname[:150] + "_" + str(abs(hash(conv)) % 100000)

        export_basename = fname + ".txt"
        out_path = os.path.join(outdir, export_basename)
        existing_path = find_export_path_for_conversation(outdir, conv)
        if existing_path is not None and os.path.basename(existing_path) == export_basename:
            out_path = existing_path
            export_basename = os.path.basename(existing_path)
        elif os.path.isfile(out_path) and not existing_export_same_conversation(out_path, conv):
            # Two different conversation ids can legitimately share the same
            # topic/title (e.g. recurring meeting series, or duplicate
            # conversation records recovered from an orphaned database).
            # Disambiguate instead of aborting the whole extraction.
            fname = fname + "_" + str(abs(hash(conv)) % 100000)
            export_basename = fname + ".txt"
            out_path = os.path.join(outdir, export_basename)
            while os.path.isfile(out_path) and not existing_export_same_conversation(out_path, conv):
                fname = fname + "_" + str(abs(hash(conv + fname)) % 100000)
                export_basename = fname + ".txt"
                out_path = os.path.join(outdir, export_basename)
        file_user_guids = {pid for pid, _name in members if pid}
        for msg in messages[conv]:
            sg = strip_worker_prefix(msg.get("from")) or strip_worker_prefix(
                msg.get("creator")
            )
            if sg:
                file_user_guids.add(sg)

        # Write whole conversation to file
        padding = 0
        with open(out_path, 'w', encoding='utf-8') as cfile:
            # Add header to file
            print(f"ConversationId: {conv}", file=cfile)
            print(f"ConversationType: {typ}", file=cfile)
            print(f"ExportFile: {export_basename}", file=cfile)
            print(f"Id: {conv}[{typ}]", file=cfile)

            if title is not None:
                print(f"Title: {title}", file=cfile)

            roster = {pid: name for pid, name in members if pid}
            for guid in file_user_guids:
                if guid not in roster and guid in mids:
                    roster[guid] = mids[guid]

            if roster:
                print("\nUserMap:", file=cfile)
                for pid in sorted(roster.keys(), key=lambda g: (roster[g] or "", g)):
                    name = roster[pid]
                    if pid and name:
                        print(f"{pid} = {name}", file=cfile)

            if len(members) > 0:
                print("\nParticipants:", file=cfile)
                for pid, name in members:
                    token = format_sender_token(pid, name)
                    if len(token) > padding and "orgid" not in token:
                        padding = len(token)
                    if pid and name and name != pid:
                        print(f"- {pid} | {name}", file=cfile)
                    else:
                        print(f"- {name}", file=cfile)

            if args.debug:
                print("\n"+json.dumps(conversations[conv], indent=4), file=cfile)

            print("", file=cfile)

            # Iterate over all messages in this conversation
            for msg in messages[conv]:
                txt = msg["content"]
                mt = msg["messageType"]

                if mt.startswith("ThreadActivity/"):
                    if mt == "ThreadActivity/AddMember":
                        root = ET.fromstring(txt)
                        ppl = []
                        for type_tag in root.findall('detailedtargetinfo/id'):
                            value = type_tag.text
                            if value in mids:
                                value = mids[value]
                            else:
                                value = ET.tostring(type_tag, encoding="unicode")
                            ppl.append(value)
                        if len(ppl) > 0:
                            txt = ",".join(ppl)
                    elif mt == "ThreadActivity/MemberJoined" or mt == "ThreadActivity/MemberLeft":
                        root = json.loads(txt)
                        ppl = []
                        for member in root["members"]:
                            value = member["id"]
                            if value in mids:
                                value = mids[value]
                            ppl.append(value)
                        if len(ppl) > 0:
                            txt = ",".join(ppl)

                    print(tm, f"{mt}", "--", txt, file=cfile)

                elif mt.startswith("Event/"):
                    if mt == "Event/Call":
                        ppl = []
                        end = ""
                        if txt.startswith("<ended/>"):
                            end = "Call ENDED: "
                            txt = txt[8:]
                        try:
                            root = ET.fromstring(txt)
                            for parter in root.findall('part'):
                                name = parter.find('displayName').text
                                if name is None:
    #                                print("tag:",parter,name,ET.tostring(parter))
                                    name = parter.find('name').text
                                if "," in name:
                                    name=" ".join(reversed([y.strip(" ") for y in name.split(",")]))
                                ppl.append(name)

                            if len(ppl) > 0:
                                txt = end+"Parted: "+",".join(ppl)
                        except Exception:
                            # Some call event XML payloads have multiple root
                            # elements or otherwise don't parse cleanly. Fall
                            # back to the raw (semi-)XML text instead of
                            # aborting the whole extraction.
                            txt = end+txt
                    print(tm, f"{mt}", "--", txt, file=cfile)
                elif typ == "Conversation":
                    tm = time.gmtime(msg["originalArrivalTime"]/1000)
                    tm = time.strftime("%Y-%m-%d %H:%M:%S", tm)
                    p = msg["properties"]
                    if "activity" in p:
                        p = p["activity"]
                    name = rname(p, "sourceUserImDisplayName", "sourceUserId")
                    txt = p["messagePreview"]
                    if "sourceThreadTopic" in p:
                        txt = "["+p["sourceThreadTopic"]+"] "+txt

                    txt = txt.rstrip("\n")
                    txt = txt.replace("\n", "\\n")
                    print(tm, f"{mt}", f"<{name}>", txt, file=cfile)

                elif typ == "Thread":
                    if "call-log" in msg["properties"]:
                        log = json.loads(msg["properties"]["call-log"])

                        tm = time.gmtime(msg["originalArrivalTime"]/1000)
                        tm = time.strftime("%Y-%m-%d %H:%M:%S", tm)

                        name = rname(log["originatorParticipant"],
                                     "displayName", "id")
                        target = log.get("targetParticipant")
                        if target is not None:
                            tname = rname(target, "displayName", "id")
                            txt = f"call to {tname}"
                        else:
                            txt = "called"

                        ps = log.get("participants")
                        if ps is not None:
                            if log["originatorParticipant"]["id"] in ps:
                                ps.remove(log["originatorParticipant"]["id"])
                            if target is not None:
                                if target["id"] in ps:
                                    ps.remove(target["id"])

                            if len(ps) > 0:
                                txt += " + " + ",".join([resolve(x) for x in ps])

                        if log.get("endTime"):
                            txt += " - End: "+log["endTime"]

                    print(tm, f"{mt}", f"<{name}>", txt, file=cfile)
                else:
                    tm = time.gmtime(msg["originalArrivalTime"]/1000)
                    tm = time.strftime("%Y-%m-%d %H:%M:%S", tm)
                    sender = sender_from_message(msg)
                    if len(sender) > padding and "orgid" not in sender:
                        padding = len(sender)

                    if txt is None:
                        if "properties" in msg:
                            if "deletetime" in msg["properties"]:
                                txt = "<deleted @"+time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(
                                    int(msg["properties"]["deletetime"])/1000))+">"
                        else:
                            print("ERR:", msg)
                            raise Exception
    #                if txt.startswith("b'"):
    #                    txt=txt[2:-1]
    #                    txt=bytes(txt, "utf-8").decode("unicode_escape")
                    if mt == "RichText/Html":
                        mt = "Html"
                        txt = h2t.handle(txt)

                    txt = txt.strip("\n")
                    txt = txt.replace("\n", "\\n")

                    print(tm, f"{mt}", f"%-{padding+2}s"%f"<{sender}>", txt, file = cfile)

        remove_other_exports_same_conversation(outdir, conv, out_path)

    json_dir = os.path.dirname(os.path.abspath(args.json))
    if json_dir and mids:
        name_to_guid = {}
        for guid, display in mids.items():
            if display and guid:
                name_to_guid[display] = guid
        map_path = os.path.join(json_dir, "userIdMap.json")
        with open(map_path, "w", encoding="utf-8") as map_file:
            json.dump(name_to_guid, map_file, indent=2, sort_keys=True)
            map_file.write("\n")

    smsg = []
    for k, v in stats.items():
        if v > 1:
            smsg.append(f"{v} {k}s")
        else:
            smsg.append(f"{v} {k}")

    smsg = ", ".join(smsg)
    print("Written", smsg)


parse_args()
run()
