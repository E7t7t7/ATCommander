import unittest
from unittest.mock import patch
from datetime import datetime, timezone

from atcommander import community_consensus as cc
from atcommander.cm_reviews import CMReview, CMReviewEngine


NOW=datetime(2026,9,30,tzinfo=timezone.utc).timestamp()


def candidate(title,body="",*,comment=False):
    return {"data":{"title":title,"selftext":body,"author":"reviewer",
                    "created_utc":NOW-86400,"is_comment":comment,
                    "permalink":"/r/AnimationThrowdown/comments/example/preview/"}}


class CommunityDiscoveryTests(unittest.TestCase):
    def test_typographic_aliases_match_whole_combo_only(self):
        for spelling in ("Professor Bender Swap","Professor‑Bender Swap","Professor — Bender Swap"):
            rows=cc._extract_ratings(candidate("CM Review",f"**{spelling}**\nRating: 4/10"),"Professor-Bender Swap")
            self.assertEqual([row.score for row in rows],[4])
        self.assertFalse(cc._name_pattern("Professor-Bender Swap").search("Professor Bender Swapover"))
        self.assertFalse(cc._name_pattern("Professor-Bender Swap").search("Professor Fry Swap"))

    def test_apostrophe_alias_and_space_dash_match_are_symmetric(self):
        self.assertTrue(cc._name_pattern("Leela’s Pong Add-on").search("Leelas Pong Add on"))
        self.assertTrue(cc._name_pattern("Leelas Pong Add on").search("Leela’s Pong Add-on"))
        self.assertEqual(cc._name_key("Leela’s Pong Add-on"),cc._name_key("Leelas Pong Add on"))

    def test_alias_rss_query_removes_typographic_punctuation(self):
        empty=type("Response",(),{"text":"<feed xmlns='http://www.w3.org/2005/Atom'/>"})()
        found=type("Response",(),{"text":"<feed xmlns='http://www.w3.org/2005/Atom'><entry><title>CM Review</title><published>2026-09-29T12:00:00Z</published><content>Professor Bender Swap: 4/10</content></entry></feed>"})()
        with patch.object(cc,"_public_get",side_effect=[empty,found]) as get:
            posts=cc._reddit_rss_search("Professor‑Bender Swap")
        self.assertEqual(get.call_count,2)
        self.assertEqual(get.call_args.args[1]["q"],"Professor Bender Swap")
        self.assertEqual(cc._aggregate("Professor‑Bender Swap",posts,730,NOW).score,4)

    def test_punctuation_variant_reuses_fresh_dated_cache(self):
        row=cc.CommunityRating(4,"reviewer","2026-09-29",url="https://www.reddit.com/r/AnimationThrowdown/comments/example/")
        evidence=cc.CommunityConsensus("Professor-Bender Swap",4,[row],status="matched",fetched_at=NOW)
        key=(cc._name_key(evidence.combo),730)
        with patch.object(cc,"_DISK_LOADED",True),patch.object(cc,"_CACHE",{key:(NOW,evidence)}),patch.object(cc.time,"time",return_value=NOW+60),patch.object(cc,"_reddit_search") as search:
            answer=cc.fetch_reddit_consensus("Professor Bender Swap")
        search.assert_not_called()
        self.assertTrue(answer.cached)
        self.assertEqual(answer.score,4)

    def test_unnamed_recent_preview_can_find_comment_only_rating(self):
        image=candidate("Upcoming stuff for the Rich BGE")
        rated=candidate("Rich preview","Rogu - **Buckarito (Punch, Burn, Sturdy)**\nExcellent. **10/10.**",comment=True)
        with patch.object(cc,"_RECENT_POOL",(NOW,[image])),patch.object(cc.time,"time",return_value=NOW),patch.object(cc,"_reddit_rss_search",return_value=[]),patch.object(cc,"_reddit_json_search") as json_search,patch.object(cc,"_comment_candidates",return_value=cc._PublicPosts([rated],scope="bounded comment feeds",partial=True)) as comments:
            answer=cc._aggregate("Buckarito",cc._reddit_search("Buckarito"),730,NOW)
        json_search.assert_not_called()
        comments.assert_called_once()
        self.assertEqual(comments.call_args.args[0],[image])
        self.assertEqual(answer.score,10)
        self.assertEqual(answer.latest_date,"2026-09-29")
        self.assertTrue(answer.partial)

    def test_unrelated_recent_posts_do_not_trigger_comment_probes(self):
        image=candidate("My mythic pull")
        with patch.object(cc,"_RECENT_POOL",(NOW,[image])),patch.object(cc.time,"time",return_value=NOW),patch.object(cc,"_reddit_rss_search",return_value=[]),patch.object(cc,"_comment_candidates") as comments:
            answer=cc._aggregate("Buckarito",cc._reddit_search("Buckarito"),730,NOW)
        comments.assert_not_called()
        self.assertEqual(answer.status,"no_match")

    def test_shared_comment_evidence_precedes_rate_limited_search(self):
        image=candidate("Upcoming stuff for the Rich BGE")
        rated=candidate("Rich preview","**Buckarito**\nRating: 10/10",comment=True)
        cache={cc._comment_url(image):(NOW-60,[rated])}
        with patch.object(cc,"_RECENT_POOL",(NOW,[image])),patch.object(cc,"_COMMENTS_CACHE",cache),patch.object(cc.time,"time",return_value=NOW),patch.object(cc,"_reddit_rss_search",side_effect=cc.RedditRateLimited("cooldown")) as search:
            answer=cc._aggregate("Buckarito",cc._reddit_search("Buckarito"),730,NOW)
        search.assert_not_called()
        self.assertEqual(answer.score,10)
        self.assertIn("cached public Reddit comment",answer.lookup_scope)

    def test_expired_comment_cache_does_not_override_live_lookup(self):
        image=candidate("Upcoming stuff for the Rich BGE")
        rated=candidate("Rich preview","**Buckarito**\nRating: 10/10",comment=True)
        cache={cc._comment_url(image):(NOW-1801,[rated])}
        with patch.object(cc,"_RECENT_POOL",(NOW,[image])),patch.object(cc,"_COMMENTS_CACHE",cache),patch.object(cc.time,"time",return_value=NOW),patch.object(cc,"_reddit_rss_search",side_effect=cc.RedditRateLimited("cooldown")) as search:
            with self.assertRaises(cc.RedditRateLimited):cc._reddit_search("Buckarito")
        search.assert_called_once()

    def test_review_candidate_comment_reads_keep_existing_two_thread_bound(self):
        posts=[]
        for index in range(8):
            row=candidate(f"CM Review {index}")
            row["data"]["permalink"]=f"/r/AnimationThrowdown/comments/thread{index}/review/"
            posts.append(row)
        empty=type("Response",(),{"text":"<feed xmlns='http://www.w3.org/2005/Atom'/>"})()
        with patch.object(cc,"_COMMENTS_CACHE",{}),patch.object(cc,"_public_get",return_value=empty) as get:
            answer=cc._comment_candidates(cc._recent_review_threads(posts),"Buckarito",5)
        self.assertEqual(len(cc._recent_review_threads(posts)),3)
        self.assertEqual(get.call_count,2)
        self.assertEqual(answer,[])


class CommunityRefreshTests(unittest.TestCase):
    def review(self,name="Buckarito",score=6.5):
        return CMReview("fixture",name,"rich",score,"Original","Current model",[],["Original reason"],["Original caution"],data_score=score)

    def evidence(self,score,status="matched"):
        return cc.CommunityConsensus("Buckarito",score,[cc.CommunityRating(score,"reviewer","2026-09-29",age_days=1)],status=status)

    def test_unavailable_refresh_clears_previous_nudge_and_rating(self):
        engine=CMReviewEngine({});review=self.review()
        base=engine.apply_community_consensus(self.review(),None)
        engine.apply_community_consensus(review,self.evidence(7.5))
        self.assertEqual(review.score,7)
        engine.apply_community_consensus(review,cc.CommunityConsensus("Buckarito",status="unavailable",note="Provider cooldown"))
        self.assertEqual(review.score,6.5)
        self.assertEqual(review.recommendation,base.recommendation)
        self.assertIsNone(review.community_score)
        self.assertEqual(review.community_count,0)
        self.assertEqual(review.community_note,"Provider cooldown")
        self.assertEqual(review.reasons,["Original reason"])

    def test_stale_refresh_restores_current_model_recommendation(self):
        engine=CMReviewEngine({});review=self.review()
        base=engine.apply_community_consensus(self.review(),None)
        engine.apply_community_consensus(review,self.evidence(7.5))
        engine.apply_community_consensus(review,self.evidence(7.5,"stale"))
        self.assertEqual(review.score,6.5)
        self.assertEqual(review.recommendation,base.recommendation)
        self.assertEqual(review.community_score,7.5)
        self.assertEqual(review.reasons,["Original reason"])

    def test_reapplying_evidence_does_not_duplicate_generated_explanation(self):
        engine=CMReviewEngine({});review=self.review()
        engine.apply_community_consensus(review,self.evidence(7.5))
        engine.apply_community_consensus(review,self.evidence(7.5))
        self.assertEqual(sum(reason.startswith("Recent explicit Reddit ratings nudged") for reason in review.reasons),1)

    def test_large_community_disagreement_preserves_calibrated_scores(self):
        engine=CMReviewEngine({})
        for name,current,community in (("Wingnut Amy",1,6),("Buckarito",6.5,10)):
            review=self.review(name,current)
            engine.apply_community_consensus(review,self.evidence(community))
            engine.apply_community_consensus(review,self.evidence(community))
            self.assertEqual(review.score,current)
            self.assertEqual(sum(caution.startswith("Dated Reddit reviews differ") for caution in review.cautions),1)


if __name__=="__main__":unittest.main()
