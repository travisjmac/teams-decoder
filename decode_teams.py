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

            # Grab display names for all id's
            cid = item["value"]["id"]
            for member in item["value"]["members"]:
                if "nameHint" in member:
                    mid = member["id"]
                    nh = member["nameHint"]
                    if "displayName" in nh:
                        mids[mid] = nh["displayName"]

            # Also grab display names from "lastMessage". Mostly needed to get own name
            if "lastMessage" in item["value"] and type(item["value"]["lastMessage"]) is dict:
                last_msg = item["value"]["lastMessage"]
                who = last_msg.get("from")
                if who is not None:
                    if who.startswith("worker/"):
                        who = who[7:]
                    imdisplayname = last_msg.get("imdisplayname")
                    if imdisplayname:
    #                print("who:",who,item["value"]["lastMessage"]["imdisplayname"])
                        mids[who] = imdisplayname

            # Keep info around for later
            conversations[cid] = item["value"]

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
                if "nameHint" in person and "displayName" in person["nameHint"]:
                    name = person["nameHint"]["displayName"]
                else:
                    name = resolve(person["id"])
                members.append(name)

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
                if "nameHint" in person and "displayName" in person["nameHint"]:
                    name = person["nameHint"]["displayName"]
                else:
                    name = resolve(person["id"])
                members.append(name)

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
            tmem = members
            # Remove myself from the filename, if present
            if args.name is not None and args.name in tmem:
                tmem.remove(args.name)
            fname = ",".join(members)

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

        if os.path.isfile(os.path.join(outdir, fname)):
            # Two different conversation ids can legitimately share the same
            # topic/title (e.g. recurring meeting series, or duplicate
            # conversation records recovered from an orphaned database).
            # Disambiguate instead of aborting the whole extraction.
            fname = fname + "_" + str(abs(hash(conv)) % 100000)

        # Write whole conversation to file
        padding = 0
        with open(os.path.join(outdir, fname), 'w', encoding='utf-8') as cfile:
            # Add header to file
            print(f"Id: {conv}[{typ}]", file=cfile)

            if title is not None:
                print(f"Title: {title}", file=cfile)

            if len(members) > 0:
                print("\nParticipants:", file=cfile)
                for name in members:
                    if len(name) > padding and not "orgid" in name:
                        padding = len(name)
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
                    name = msg["imDisplayName"]

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

                    print(tm, f"{mt}", f"%-{padding+2}s"%f"<{name}>", txt, file = cfile)

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
