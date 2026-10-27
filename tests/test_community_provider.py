import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone

from atcommander import community_consensus as cc
from atcommander.cm_reviews import CMReview, CMReviewEngine


NOW=datetime(2026,9,30,tzinfo=timezone.utc).timestamp()
def post(body, author="author", day="2026-09-15"):
    return {"title":"CM Review 9/15/2026","selftext":body,"author":author,"created_utc":datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp(),"permalink":"/r/AnimationThrowdown/comments/test/"}


class CommunityTests(unittest.TestCase):
    def test_sections_do_not_borrow_next_combo_score(self):
        body="**Wingnut Amy**\nA weak old card.\n\n**Buckarito**\nRating: 8/10"
        self.assertEqual(cc._extract_ratings(post(body),"Wingnut Amy"), [])
    def test_html_headings_survive_feed_parsing(self):
        body=cc._strip_html("<p><strong>Wingnut Amy</strong></p><p>Rating: 1/10</p><p><strong>Buckarito</strong></p><p>Rating: 8/10</p>")
        self.assertEqual(cc._extract_ratings(post(body),"Wingnut Amy")[0].score,1)
    def test_historic_and_current_scores_coexist(self):
        body="**Wingnut Amy**\n9/1/2025 6 (oldauthor)\nI give this a 1/10 today."
        ratings=cc._extract_ratings(post(body),"Wingnut Amy")
        self.assertEqual({r.score for r in ratings},{1,6})
        self.assertEqual({r.rating_date for r in ratings},{"2025-09-01","2026-09-15"})
    def test_historic_reposts_are_not_independent_votes(self):
        posts=[post("**Wingnut Amy**\n9/1/2026 1 (sameauthor)",author="reposter1"),post("**Wingnut Amy**\n9/1/2026 1 (sameauthor)",author="reposter2")]
        answer=cc._aggregate("Wingnut Amy",posts,730,NOW)
        self.assertEqual(answer.count,1)
    def test_latest_per_author_wins(self):
        answer=cc._aggregate("Wingnut Amy",[post("**Wingnut Amy**\n9/1/2025 6 (sameauthor)\n9/1/2026 1 (sameauthor)")],730,NOW)
        self.assertEqual(answer.score,1)
    def test_future_unknown_and_old_dates_excluded(self):
        answer=cc._aggregate("Wingnut Amy",[post("**Wingnut Amy**\n10/1/2026 8 (future)\n1/1/2020 9 (ancient)")],730,NOW)
        self.assertEqual(answer.status,"no_match")
    def test_empty_feed_is_not_network_failure(self):
        response=type("Response",(),{"text":"<feed xmlns='http://www.w3.org/2005/Atom'></feed>"})()
        with patch.object(cc,"_public_get",return_value=response):
            self.assertEqual(cc._reddit_search("No Such Combo"),[])
    def test_failure_is_distinct_from_no_match(self):
        with patch.object(cc,"_reddit_search",side_effect=cc.RedditUnavailable("HTTP 403")):
            answer=cc.fetch_reddit_consensus("Unique Missing Test Combo",cache_seconds=0)
        self.assertEqual(answer.status,"unavailable")
        self.assertIn("could not be reached",answer.note)
    def test_data_score_retained_on_large_disagreement(self):
        review=CMReview("1","Wingnut Amy","athletic",1,"Skip","Weak",[],[],[],data_score=1)
        consensus=cc.CommunityConsensus("Wingnut Amy",score=6,ratings=[cc.CommunityRating(6,"author","2026-09-15",age_days=15)],status="matched")
        answer=CMReviewEngine({}).apply_community_consensus(review,consensus)
        self.assertEqual(answer.score,1)
        self.assertEqual(answer.community_score,6)
    def test_stale_evidence_never_adjusts_model(self):
        review=CMReview("1","Test","athletic",6,"Buy","Good",[],[],[],data_score=6)
        consensus=cc.CommunityConsensus("Test",score=7,ratings=[cc.CommunityRating(7,"author","2026-09-15",age_days=15)],status="stale")
        self.assertEqual(CMReviewEngine({}).apply_community_consensus(review,consensus).score,6)

    def test_recent_feed_is_shared_across_combos(self):
        response=type("Response",(),{"text":"<feed xmlns='http://www.w3.org/2005/Atom'><entry><title>CM Review</title><published>2026-09-15T12:00:00Z</published><content>Wingnut Amy: 1/10; Buckarito: 7/10</content></entry></feed>"})()
        with patch.object(cc,"_RECENT_POOL",None),patch.object(cc,"_public_get",return_value=response) as fetch:
            self.assertTrue(cc._reddit_search("Wingnut Amy"))
            self.assertTrue(cc._reddit_search("Buckarito"))
            fetch.assert_called_once()

    def test_public_evidence_survives_restart_and_expires(self):
        rating=cc.CommunityRating(6,"reviewer","2026-09-15",url="https://www.reddit.com/r/AnimationThrowdown/comments/test/")
        evidence={("wingnut amy",730):(NOW,cc.CommunityConsensus("Wingnut Amy",6,[rating],status="matched"))}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"evidence.json"
            cc._write_evidence_cache(path,evidence,NOW)
            restored=cc._read_evidence_cache(path,NOW+60)
            self.assertEqual(restored[("wingnut amy",730)][1].ratings[0].score,6)
            self.assertEqual(cc._read_evidence_cache(path,NOW+8*86400),{})

    def test_restored_evidence_rechecks_review_age_and_aggregate(self):
        rating=cc.CommunityRating(6,"reviewer","2026-09-15",url="https://www.reddit.com/r/AnimationThrowdown/comments/test/",age_days=0)
        evidence={("test",20):(NOW,cc.CommunityConsensus("Test",9,[rating],status="matched"))}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"evidence.json"
            cc._write_evidence_cache(path,evidence,NOW)
            restored=cc._read_evidence_cache(path,NOW+86400)[("test",20)][1]
            self.assertEqual(restored.score,6)
            self.assertEqual(restored.ratings[0].age_days,16)
            self.assertEqual(cc._read_evidence_cache(path,NOW+6*86400),{})

    def test_preview_character_heading_terminal_score(self):
        body="Rogu - **Buckarito (Punch, Burn, Sturdy)**\nGood damage. **10/10.**\n\nCarter - **Other Combo (Heal, Punch)**\nWeak. **2/10.**"
        rows=cc._extract_ratings(post(body),"Buckarito")
        self.assertEqual([row.score for row in rows],[10])

    def test_terminal_rating_on_a_passing_reference_is_not_borrowed(self):
        body="I compared Buckarito with my main combo. My deck is 10/10."
        self.assertEqual(cc._extract_ratings(post(body),"Buckarito"),[])

    def test_both_historic_and_later_current_sections_are_read(self):
        body="**Buckarito**\n9/1/2025 8 (oldreviewer)\n\n**Other Combo**\nRating: 1/10\n\nBuckarito 1000 stones Score 7.5\nCurrent review.\n\nOther Combo 500 gems Score 3"
        rows=cc._extract_ratings(post(body),"Buckarito")
        self.assertEqual({row.score for row in rows},{8,7.5})

    def test_comment_date_does_not_inherit_submission_date(self):
        candidate=post("**Buckarito**\nRating: 8/10",day="2026-09-16")
        candidate["is_comment"]=True
        row=cc._extract_ratings(candidate,"Buckarito")[0]
        self.assertEqual(row.rating_date,"2026-09-16")
        self.assertIn("comment",row.source)

    def test_recent_passing_reference_falls_back_to_dated_search(self):
        recent=[{"data":post("I like Buckarito.")}]
        rated=[{"data":post("**Buckarito**\nRating: 8/10")}]
        with patch.object(cc,"_RECENT_POOL",(NOW,recent)),patch.object(cc.time,"time",return_value=NOW),patch.object(cc,"_reddit_rss_search",return_value=rated) as search:
            answer=cc._aggregate("Buckarito",cc._reddit_search("Buckarito"),730,NOW)
        search.assert_called_once()
        self.assertEqual(answer.score,8)

    def test_old_rating_in_recent_repost_does_not_block_search(self):
        recent=[{"data":post("**Buckarito**\n1/1/2020 10 (oldreviewer)")}]
        rated=[{"data":post("**Buckarito**\nRating: 8/10")}]
        with patch.object(cc,"_RECENT_POOL",(NOW,recent)),patch.object(cc.time,"time",return_value=NOW),patch.object(cc,"_reddit_rss_search",return_value=rated) as search:
            answer=cc._aggregate("Buckarito",cc._reddit_search("Buckarito"),730,NOW)
        search.assert_called_once()
        self.assertEqual(answer.score,8)

    def test_stale_shared_feed_is_kept_when_refresh_is_rate_limited(self):
        recent=[{"data":post("**Buckarito**\nRating: 8/10")}]
        with patch.object(cc,"_RECENT_POOL",(NOW-2000,recent)),patch.object(cc,"_POOL_RETRY_AT",0),patch.object(cc.time,"time",return_value=NOW),patch.object(cc,"_public_get",side_effect=cc.RedditRateLimited("cooldown")) as get:
            answer=cc._aggregate("Buckarito",cc._reddit_search("Buckarito"),730,NOW)
        get.assert_called_once()
        self.assertEqual(answer.status,"stale")
        self.assertEqual(answer.score,8)
        self.assertTrue(answer.partial)
        self.assertIn("does not adjust",answer.note)

    def test_json_fallback_recovers_unavailable_rss(self):
        rated=cc._PublicPosts([{"data":post("**Buckarito**\nRating: 8/10")}],scope="JSON")
        with patch.object(cc,"_RECENT_POOL",(NOW,[])),patch.object(cc.time,"time",return_value=NOW),patch.object(cc,"_reddit_rss_search",side_effect=cc.RedditUnavailable("RSS 403")),patch.object(cc,"_reddit_json_search",return_value=rated):
            answer=cc._aggregate("Buckarito",cc._reddit_search("Buckarito"),730,NOW)
        self.assertEqual(answer.score,8)
        self.assertIn("JSON",answer.lookup_scope)

    def test_image_submission_can_find_public_comment_rating(self):
        image={"data":post("",day="2026-09-14")}
        image["data"]["title"]="New rich combos"
        comment={"data":post("Rogu - **Buckarito (Punch, Burn, Sturdy)**\nGood. **10/10.**",author="commentreviewer",day="2026-09-16")}
        comment["data"]["is_comment"]=True
        comments=cc._PublicPosts([comment],scope="bounded comment feeds",partial=True)
        with patch.object(cc,"_RECENT_POOL",(NOW,[])),patch.object(cc.time,"time",return_value=NOW),patch.object(cc,"_reddit_rss_search",return_value=[image]),patch.object(cc,"_reddit_json_search",return_value=cc._PublicPosts([image])),patch.object(cc,"_comment_candidates",return_value=comments) as fetch:
            answer=cc._aggregate("Buckarito",cc._reddit_search("Buckarito"),730,NOW)
        fetch.assert_called_once()
        self.assertEqual(answer.count,1)
        self.assertEqual(answer.ratings[0].author,"commentreviewer")
        self.assertEqual(answer.latest_date,"2026-09-16")

    def test_comment_fetches_are_bounded_and_rate_limit_ends_them(self):
        candidates=[{"data":{"permalink":f"/r/AnimationThrowdown/comments/test{i}/title/"}} for i in range(5)]
        empty=type("Response",(),{"text":"<feed xmlns='http://www.w3.org/2005/Atom'></feed>"})()
        with patch.object(cc,"_COMMENTS_CACHE",{}),patch.object(cc,"_public_get",return_value=empty) as fetch:
            self.assertEqual(cc._comment_candidates(candidates,"Buckarito",5),[])
            self.assertEqual(fetch.call_count,2)
        with patch.object(cc,"_COMMENTS_CACHE",{}),patch.object(cc,"_public_get",side_effect=cc.RedditRateLimited("cooldown")) as fetch:
            answer=cc._comment_candidates(candidates,"Buckarito",5)
            fetch.assert_called_once()
            self.assertIn("cooldown",answer.error)

    def test_incomplete_comment_lookup_does_not_claim_no_match(self):
        image={"data":post("")}
        with patch.object(cc,"_RECENT_POOL",(NOW,[])),patch.object(cc.time,"time",return_value=NOW),patch.object(cc,"_reddit_rss_search",return_value=[image]),patch.object(cc,"_reddit_json_search",return_value=cc._PublicPosts([image])),patch.object(cc,"_comment_candidates",return_value=cc._PublicPosts(error="HTTP 403")):
            with self.assertRaises(cc.RedditUnavailable):cc._reddit_search("Buckarito")

    def test_feed_accepts_rss_dates_and_recognizes_comment_identity(self):
        rss="<rss><channel><item><title>Review</title><link>https://www.reddit.com/r/AnimationThrowdown/comments/test/title/comment/</link><pubDate>Tue, 15 Sep 2026 12:00:00 GMT</pubDate><description>Buckarito: 8/10</description></item></channel></rss>"
        item=cc._atom_posts(rss,"Buckarito",5)[0]["data"]
        self.assertGreater(item["created_utc"],0)
        self.assertTrue(item["is_comment"])

    def test_long_retry_after_is_respected_without_second_request(self):
        response=type("Response",(),{"status_code":429,"headers":{"Retry-After":"1200"}})()
        with patch.object(cc,"_RETRY_AT",0),patch.object(cc,"_NEXT_REQUEST",0),patch.object(cc.time,"time",return_value=NOW),patch.object(cc.requests,"get",return_value=response) as get:
            with self.assertRaises(cc.RedditRateLimited):cc._public_get("https://www.reddit.com",{},"application/json",5)
            self.assertEqual(cc._RETRY_AT,NOW+1200)
            with self.assertRaises(cc.RedditRateLimited):cc._public_get("https://www.reddit.com",{},"application/json",5)
            get.assert_called_once()

    def test_in_memory_stale_evidence_rechecks_age_window(self):
        rating=cc.CommunityRating(8,"reviewer","2026-09-15",url="https://www.reddit.com/r/AnimationThrowdown/comments/test/")
        good=cc.CommunityConsensus("Unique Expiring Evidence",8,[rating],status="matched")
        key=("unique expiring evidence",15)
        with patch.object(cc,"_DISK_LOADED",True),patch.object(cc,"_CACHE",{}),patch.object(cc,"_LAST_GOOD",{key:(NOW,good)}),patch.object(cc.time,"time",return_value=NOW+86400),patch.object(cc,"_reddit_search",side_effect=cc.RedditUnavailable("HTTP 403")):
            answer=cc.fetch_reddit_consensus("Unique Expiring Evidence",max_age_days=15)
        self.assertEqual(answer.status,"unavailable")
        self.assertIsNone(answer.score)

    def test_cached_confidence_is_recomputed_from_real_reviewers(self):
        rating=cc.CommunityRating(8,"reviewer","2026-09-15",url="https://www.reddit.com/r/AnimationThrowdown/comments/test/")
        evidence={("test",730):(NOW,cc.CommunityConsensus("Test",8,[rating],confidence="High",status="matched"))}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"evidence.json"
            cc._write_evidence_cache(path,evidence,NOW)
            self.assertEqual(cc._read_evidence_cache(path,NOW)[("test",730)][1].confidence,"Low")

    def test_fresh_disk_evidence_avoids_network_after_restart(self):
        rating=cc.CommunityRating(8,"reviewer","2026-09-15",url="https://www.reddit.com/r/AnimationThrowdown/comments/test/")
        evidence={("test",730):(NOW,cc.CommunityConsensus("Test",8,[rating],status="matched",fetched_at=NOW))}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"evidence.json"
            cc._write_evidence_cache(path,evidence,NOW)
            with patch.object(cc,"_PUBLIC_CACHE_PATH",path),patch.object(cc,"_DISK_LOADED",False),patch.object(cc,"_CACHE",{}),patch.object(cc,"_LAST_GOOD",{}),patch.object(cc.time,"time",return_value=NOW+60),patch.object(cc,"_reddit_search") as search:
                answer=cc.fetch_reddit_consensus("Test")
                search.assert_not_called()
                self.assertTrue(answer.cached)
                self.assertEqual(answer.score,8)
                self.assertIn("no new provider request",answer.note)

    def test_shared_feed_failure_is_not_retried_per_shop_combo(self):
        with patch.object(cc,"_RECENT_POOL",None),patch.object(cc,"_POOL_RETRY_AT",0),patch.object(cc.time,"time",return_value=NOW),patch.object(cc,"_public_get",side_effect=cc.RedditUnavailable("403")) as fetch,patch.object(cc,"_reddit_rss_search",return_value=[]):
            cc._reddit_search("Missing A")
            cc._reddit_search("Missing B")
            fetch.assert_called_once()
