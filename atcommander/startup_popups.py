"""Bounded startup reward navigation and fresh, guarded reward choices.

The native inspector reads the current public popup manager's visible list. It
never reads account strings or returns pointers. Only visibly offered ordinary
reward navigation can be dispatched; SDK, purchase, claim and reroll handlers
are absent. Pending server choices use the existing card-priority helpers.
"""
from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Mapping

from .local_commands import CommandUnavailable
from .shop_rewards import api_rejected, choose_item_reward, extract_item_choices, item_choice_confirmed
from .startup_popup_bindings import PUBLIC_BUILD_1182, PUBLIC_WASM_1182
from .startup_popup_bindings_1183 import PUBLIC_BUILD_1183, PUBLIC_WASM_1183


PUBLIC_WASM_SHA256 = "f337d37ff520406d2b40257f351e9417c825fbfd66d32dd1023d990b1afbc88c"
MAX_ACTIONS = 24
MAX_CHOICES = 24
MAX_WAIT_SECONDS = 90.0
_MESSAGE = "Startup rewards could not be settled safely; no ad was started."
_GEMS = (("user_data", "free_hard_currency"),
         ("common_fields", "free_hard_currency_balance"),
         ("common_fields", "hard_currency_balance"))

# Exact field indices, offsets, names and namespaces from the copied public
# metadata version39, corroborated by the public native methods. Generic List
# classes are checked only through these scoped fields, never globally chosen.
PUBLIC_TYPES = {
    "Managers": {"offset": 733187, "namespace_offset": 576896, "namespace": "", "fields": [[2, "<Popup>k__BackingField", 8]]},
    "PopupManager": {"offset": 744409, "namespace_offset": 576896, "namespace": "", "fields": [[8, "_visiblePopups", 52]]},
    "Popup": {"offset": 735089, "namespace_offset": 576896, "namespace": "", "fields": [[4, "CanClose", 18], [11, "<State>k__BackingField", 56], [17, "_OnHide", 80], [18, "_OnBeforeHide", 84]]},
    "RewardPopup": {"offset": 764704, "namespace_offset": 576896, "namespace": "", "fields": [[35, "OnDismiss", 208], [42, "<IsTutorialFlow>k__BackingField", 236], [43, "<BuyMoreClicked>k__BackingField", 237], [57, "_buttonsView", 292], [64, "_openAnimationController", 320], [65, "_mustWaitShowRoutine", 324]]},
    "RewardChoicePopup": {"offset": 764686, "namespace_offset": 576896, "namespace": "", "fields": [[6, "_buttonsView", 120], [18, "rewardData", 168]]},
    "ServiceRewardChoiceData": {"offset": 670021, "namespace_offset": 576896, "namespace": "", "fields": [[1, "<Type>k__BackingField", 12]]},
    "QuestCompletePopup": {"offset": 764501, "namespace_offset": 576896, "namespace": "", "fields": [[2, "Loading", 108], [3, "RewardBox", 112], [4, "_questData", 116], [5, "_onAfterRewards", 120], [6, "_hasClickedBox", 124], [7, "_autoComplete", 125]]},
    "ServiceAchievementData": {"offset": 611868, "namespace_offset": 576896, "namespace": "", "fields": [[14, "<Progress>k__BackingField", 68], [15, "<MaxProgress>k__BackingField", 72], [16, "<Status>k__BackingField", 76]]},
    "ServiceDeepLinkData": {"offset": 647906, "namespace_offset": 576896, "namespace": "", "fields": [[0, "<ID>k__BackingField", 8]]},
    "ServiceRewardData": {"offset": 670567, "namespace_offset": 576896, "namespace": "", "fields": [[0, "<IsInRewardsReceivedFormat>k__BackingField", 8], [1, "_contents", 12]]},
    "<>c__DisplayClass15_0": {"offset": 695879, "namespace_offset": 576896, "namespace": "", "scoped": True, "fields": [[0, "<>4__this", 8], [1, "reward", 12]]},
    "Delegate": {"offset": 1007798, "namespace_offset": 999978, "namespace": "System", "fields": [[0, "method_ptr", 8], [2, "m_target", 16], [3, "method", 20], [6, "method_code", 32]]},
    "MulticastDelegate": {"offset": 1046998, "namespace_offset": 999978, "namespace": "System", "fields": [[0, "delegates", 60]]},
    "Action`1": {"offset": 1006212, "namespace_offset": 999978, "namespace": "System", "scoped": True, "fields": []},
    "SkeletonAnimation": {"offset": 1830395, "namespace_offset": 576896, "namespace": "", "fields": [[0, "state", 112]]},
    "ResearchPopup": {"offset": 764598, "namespace_offset": 576896, "namespace": "", "fields": [[6, "_library", 124], [38, "<Index>k__BackingField", 252], [41, "_isAdLocked", 258]]},
    "ResearchProgressPopup": {"offset": 764612, "namespace_offset": 576896, "namespace": "", "fields": [[15, "<Index>k__BackingField", 160], [24, "_adSpeedUpsData", 196]]},
    "AdBoostPopup": {"offset": 762783, "namespace_offset": 576896, "namespace": "", "fields": [[0, "_researchProgressPopup", 100], [1, "_adPodSpeedBonus", 104]]},
    "WatchAdsPopup": {"offset": 765121, "namespace_offset": 576896, "namespace": "", "fields": [[3, "Content", 112], [4, "Loading", 116], [11, "_trackingContext", 144]]},
    "ShopPopup": {"offset": 764936, "namespace_offset": 576896, "namespace": "", "fields": [[6, "Tabs", 124], [26, "_scrollingTab", 220], [31, "currentCategoryId", 240]]},
    "SaleLastChanceOfferPopup": {"offset": 892834, "namespace_offset": 576896, "namespace": "", "fields": [[1, "_closeButton", 100], [17, "_buyButton", 164], [20, "_storeItem", 188]]},
    "RewardListPopup": {"offset": 888366, "namespace_offset": 576896, "namespace": "", "fields": [[0, "_closeButton", 100]]},
    "RewardPopupButtonsView": {"offset": 903785, "namespace_offset": 576896, "namespace": "", "fields": [[0, "_editorButtonMap", 16]]},
    "ButtonToGameObject": {"offset": 904053, "namespace_offset": 576896, "namespace": "", "fields": [[0, "ButtonType", 8], [1, "ButtonObject", 12]]},
    "RewardPopupOpeningAnimationController": {"offset": 904129, "namespace_offset": 576896, "namespace": "", "fields": [[0, "_unpauseButton", 16], [3, "_paused", 28], [4, "_held", 29]]},
    "tk2dUIItem": {"offset": 945314, "namespace_offset": 576896, "namespace": "", "fields": [[12, "sendMessageTarget", 64], [15, "SendMessageOnClickMethodName", 76]]},
    "GameObject": {"offset": 985534, "namespace_offset": 1678346, "namespace": "UnityEngine", "fields": []},
    "List`1": {"offset": 1172793, "namespace_offset": 1171887, "namespace": "System.Collections.Generic", "fields": [[0, "_items", 8], [1, "_size", 12], [2, "_version", 16]]},
}

# Names below are copied public metadata declarations. They are used only to
# identify an object already owned by the visible popup list, never to read an
# arbitrary native string or search for arbitrary objects.
PUBLIC_POPUP_NAMES = {
    "AccountRecoveryPopup": 763329,
    "AdBoostPopup": 762783,
    "AdConsentPopup": 762796,
    "AdCratePopup": 762811,
    "AdventCalendarRewardListPopup": 765490,
    "AdventureIslandPopup": 762824,
    "AdventureResultsPopup": 762875,
    "AnnouncementPopup": 762765,
    "ArenaResultsPopup": 762897,
    "AssetBundleDownloadPopup": 762915,
    "AutoBattleListPopup": 762511,
    "BackfillAdPopup": 762940,
    "BattleResultsPopup": 834779,
    "BGEAffectedCardsPopup": 762956,
    "BGEAnnouncementPopup": 762978,
    "BlackMarketConfirmationPopup": 762999,
    "BossSelectPopup": 763028,
    "BundleChoicePopup": 763044,
    "CallToActionPopup": 763401,
    "CardArchivePopup": 763062,
    "CardBaseInfoPopup": 837651,
    "CardFilterPopup": 763079,
    "CardInfoPopup": 763095,
    "CardListPopup": 763419,
    "CardRarityInfoPopup": 763447,
    "CardReceivedPopup": 763467,
    "CardSkillTutorialPopup": 765533,
    "ChapterSelectPopup": 763485,
    "CharacterSpeechPopup": 763504,
    "ChatPopup": 710718,
    "ClaimDailyRewardsPopup": 763548,
    "CollectionClaimReminderPopup": 765556,
    "CollectionInfoPopup": 765419,
    "CollectionMilestonePopup": 765439,
    "CollectionRewardListPopup": 765464,
    "CollectionsAndTitlesAnnouncementPopup": 847856,
    "ComboGridPopup": 763571,
    "ComboRecipePopup": 763586,
    "ConfirmPopup": 763388,
    "ConfirmSyncPopup": 763371,
    "COPPACompliancePopup": 763350,
    "CraftCardPopup": 763314,
    "CreateGuildPopup": 763109,
    "DailyRewardsPopup": 763126,
    "DeckRenamePopup": 763144,
    "DeckSelectPopup": 763160,
    "DefeatPopup": 763193,
    "DiscoveredCardMilestonePopup": 765618,
    "EditDeckPopup": 584320,
    "EventCardsPopup": 763205,
    "EventClaimRewardsPopup": 763221,
    "EventInfoPopup": 762547,
    "EventMilestoneCompletePopup": 763244,
    "EventNewsPopup": 590458,
    "EventRequirementPopup": 763603,
    "FoilInfoPopup": 862092,
    "FoilSelectPopup": 763298,
    "FragmentCallToActionPopup": 762562,
    "FullScreenEventPopup": 762588,
    "FullScreenPopup": 765217,
    "FullScreenStoreItemPopup": 762609,
    "FusionPopup": 765062,
    "GuildDetailsPopup": 763625,
    "GuildIslandPopup": 863929,
    "GuildMatchRankingsPopup": 763660,
    "GuildPopup": 722894,
    "GuildSiegeHistoryPopup": 763684,
    "GuildSiegeIntroPopup": 763707,
    "GuildSiegeIslandPopup": 763728,
    "GuildWarInfoPopup": 763750,
    "GuildWarMatchResultPopup": 763768,
    "HeroSquadBattlePopup": 763793,
    "HeroSquadInfoPopup": 763814,
    "HeroSquadIntroPopup": 763833,
    "IncompleteDeckPopup": 763853,
    "InviteToGuildPopup": 763873,
    "ItemsPopup": 762634,
    "JoinGuildPopup": 722879,
    "KickBanGuildPopup": 763892,
    "LeaderInfoBasePopup": 868842,
    "LeaderInfoCombatPopup": 763926,
    "LeaderInfoPopup": 763910,
    "LeaderSalePopup": 763948,
    "LeaderSelectPopup": 765647,
    "LeaderTrainingCompletePopup": 587727,
    "LeaderTrainingPopup": 763964,
    "LeaderTrainingProgressPopup": 762457,
    "LeaderUnlockPopup": 763984,
    "LevelUpPopup": 765520,
    "LoadingPopup": 764002,
    "LootBoosterPopup": 762645,
    "MailboxPopup": 872540,
    "MasteryCompletePopup": 764015,
    "MasteryConfirmationPopup": 764036,
    "MasteryPopup": 764061,
    "MasteryProgressPopup": 764074,
    "MaxFusionPreviewPopup": 764095,
    "MegaquestAnnouncementPopup": 874930,
    "MegaQuestReminderPopup": 762675,
    "MegaQuestTierPurchaseConfirmPopup": 764144,
    "MessagePopup": 764178,
    "MomBoxConfirmationPopup": 762728,
    "AdventureMultiAutoBattlePopup": 762845,
    "EventMultiAutoBattlePopup": 763272,
    "MultiAutoBattlePopup": 764191,
    "PVPMultiAutoBattlePopup": 764460,
    "NameChangePopup": 764212,
    "NewCardAnnouncementPopup": 764228,
    "NewEventAnnouncementPopup": 764253,
    "NewUpdatePopup": 764279,
    "OptionsPopup": 764294,
    "OutOfItemPopup": 764307,
    "PinnedMessagePopup": 878514,
    "PlatinumWelcomePopup": 764322,
    "PlayerProfilePopup": 764343,
    "Popup": 735089,
    "PostBattleSuggestionPopup": 762485,
    "PrizeCardPopup": 764362,
    "PrizePoolPopup": 764377,
    "PVEChallengeResultsPopup": 881703,
    "PVPChallengeResultsPopup": 881746,
    "PvPLeaderboardPopup": 881771,
    "QuestCompletePopup": 764501,
    "QuestsPopup": 698681,
    "RateUs5StarPopup": 764520,
    "RechargePopup": 764537,
    "RecoverRecycledCardPopup": 764551,
    "ResearchCompletePopup": 764576,
    "ResearchPopup": 764598,
    "ResearchProgressPopup": 764612,
    "RestartKongPopup": 764634,
    "ResultsPopup": 765074,
    "ResumeBattlePopup": 764651,
    "RevealCardsPopup": 764669,
    "RewardChoicePopup": 764686,
    "RewardListPopup": 888366,
    "RewardPopup": 764704,
    "SaleBundleListAlternativePopup": 764762,
    "SaleBundleListPopup": 764793,
    "SaleCollectionPackPopup": 891565,
    "SaleDoubleBundleListPopup": 764813,
    "SaleItemPackPopup": 764902,
    "SaleItemPopup": 764839,
    "SaleItemVipPassPopup": 764881,
    "SaleLastChanceOfferPopup": 892834,
    "SaleStarterPackBundlePopup": 893234,
    "ScoringPopup": 762698,
    "SFCResultsPopup": 764920,
    "ShopPopup": 764936,
    "ShopSliderPopup": 764946,
    "SiegeIslandsSelectPopup": 764962,
    "SliderPopup": 764986,
    "StepBoxPopup": 764998,
    "StepBoxTiersPopup": 765029,
    "StepBoxTrackPopup": 765011,
    "TieredIAPPopup": 765047,
    "TitleSelectPopup": 763176,
    "VictoryPopup": 765087,
    "VIPRankPopup": 762752,
    "VSPopup": 765113,
    "WatchAdsPopup": 765121,
    "WelcomeBackComboPassTrialPopup": 765370,
    "WinStreakPopup": 765135,
    "AbstractNotificationPopup": 922996,
    "SimpleNotificationPopup": 923472
}


PUBLIC_BUILD_BINDINGS = {
    PUBLIC_WASM_SHA256: {
        "types": PUBLIC_TYPES, "names": PUBLIC_POPUP_NAMES,
        "managers": 7034096, "quest_received_method": 7142816,
        "slots": {"active": 5538, "component_object": 2825, "touch": 33627, "hide": 33642,
                  "reveal_press": 34876, "reveal_release": 34877, "reward_close": 34098,
                  "reward_continue": 34099, "choice_close": 34019, "choice_continue": 34020,
                  "quest_received": 33729},
    },
    PUBLIC_WASM_1182: PUBLIC_BUILD_1182,
    PUBLIC_WASM_1183: PUBLIC_BUILD_1183,
}

_NATIVE_JS = r'''(async()=>{
 const request=__REQUEST__, builds=__BUILDS__, fail=reason=>({ok:false,reason});
 const loading=verified=>({ok:true,verified_public_build:verified,runtime_loading:true,panel:'loading',navigation_action:'wait',stack_revision:0,visible_count:0,popup_state:0});
 const m=(window.unityInstance||window.gameInstance)?.Module;
 if(!m?.HEAPU32||!m.HEAPU8||!m.wasmBinary)return loading(false);
 if(!globalThis.crypto?.subtle)return fail('runtime_unavailable');
 if(!Number.isInteger(m.wasmBinary.byteLength)||m.wasmBinary.byteLength<8||m.wasmBinary.byteLength>100*1024*1024)return fail('build_unavailable');
 let digest;try{digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',m.wasmBinary))).map(n=>n.toString(16).padStart(2,'0')).join('');}catch{return fail('build_unavailable');}
 const binding=builds[digest];if(!binding)return fail('build_changed');
 const definitions=binding.types,popupNames=binding.names,slots=binding.slots;
 const field=(name,label)=>definitions[name].fields.find(item=>item[1]===label)[2];
 const h=m.HEAPU32,b=m.HEAPU8,t=m.asm?.__indirect_function_table;
 if(h.byteLength!==b.byteLength||b.byteLength<4096||b.byteLength>768*1024*1024||!t?.get)return fail('runtime_unavailable');
 const until=performance.now()+12000;
 const valid=(p,size=512)=>Number.isInteger(p)&&p>=4096&&p%4===0&&p+size<=b.length;
 const read=p=>valid(p,4)?h[p>>>2]:null;
 const exact=(p,text)=>Number.isInteger(p)&&p>=4096&&p+text.length<b.length&&b[p+text.length]===0&&Array.from(text).every((c,i)=>b[p+i]===c.charCodeAt(0));
 const bool=(p,offset)=>valid(p,offset+1)&&b[p+offset]<=1?!!b[p+offset]:null;
 const fn=slot=>{const method=t.get(slot);if(typeof method!=='function')throw Error('unavailable');return method;};
 let base=null;
 for(let i=0;i<h.length-95;i++){
  if(!(i%65536)&&performance.now()>until)return fail('scan_timeout');
  if(h[i]===0xFAB11BAF&&h[i+1]===39&&h[i+2]===380){if(base!==null)return fail('metadata_ambiguous');base=i*4;}
 }
 if(base===null)return loading(true);
 const addresses=new Map(),candidates={},classes={};
 for(const [name,d] of Object.entries(definitions)){
  if(!exact(base+d.offset,name)||!exact(base+d.namespace_offset,d.namespace))return fail('metadata_changed');
  if(name!=='List`1'&&!d.scoped){addresses.set(base+d.offset,name);candidates[name]=[];}
 }
 const classMatches=(klass,name)=>{
  if(!valid(klass))return false;const d=definitions[name],fields=read(klass+64);
  if(!exact(read(klass+8),name)||!exact(read(klass+12),d.namespace))return false;
  return d.fields.every(([index,field,offset])=>valid(fields+index*20,20)&&exact(read(fields+index*20),field)&&read(fields+index*20+12)===offset);
 };
 for(let i=2;i<h.length-128;i++){
  if(!(i%65536)&&performance.now()>until)return fail('scan_timeout');
  const name=addresses.get(h[i]);if(!name)continue;
  const klass=i*4-8;if(!classMatches(klass,name))continue;
  if(!candidates[name].includes(klass))candidates[name].push(klass);
  if(candidates[name].length>1)return fail('class_ambiguous');
 }
 for(const [name,values] of Object.entries(candidates))if(values.length===1)classes[name]=values[0];
 for(const name of ['Managers','PopupManager','Popup','GameObject'])if(!classes[name])return loading(true);
 const recognized=(p,name)=>valid(p)&&!!classes[name]&&read(p)===classes[name];
 const derived=(p,name)=>{
  if(!valid(p)||!classes[name])return false;
  for(let klass=read(p),depth=0;depth<8&&valid(klass);depth++,klass=read(klass+44))if(klass===classes[name])return true;
  return false;
 };
 const gameActive=p=>{
  if(!recognized(p,'GameObject')||!valid(read(p+8),16))return null;
  try{const value=fn(slots.active)(p,0);return value===1?true:value===0?false:null;}catch{return null;}
 };
 const componentActive=p=>{
  if(!valid(p)||!valid(read(p+8),16))return null;
  try{return gameActive(fn(slots.component_object)(p,0));}catch{return null;}
 };
 const list=(p,max)=>{
  if(!valid(p,20)||!classMatches(read(p),'List`1'))return null;
  const array=read(p+8),count=read(p+12),revision=read(p+16);
  if(!Number.isInteger(count)||count>max||!Number.isInteger(revision)||revision>2147483647||!valid(array,16+count*4)||read(array+12)<count)return null;
  return {array,count,revision};
 };
 const managers=read(binding.managers);if(managers!==classes.Managers)return fail('manager_reference_changed');
 const statics=read(managers+92);if(statics===0)return loading(true);if(!valid(statics,12))return fail('manager_unavailable');
 const manager=read(statics+8);if(manager===0)return loading(true);if(!recognized(manager,'PopupManager'))return fail('manager_unavailable');
 const visiblePointer=read(manager+52);if(visiblePointer===0)return loading(true);
 const visible=list(visiblePointer,16);if(!visible)return fail('visible_list_unavailable');
 const common={ok:true,verified_public_build:true,stack_revision:visible.revision,visible_count:visible.count};
 const active=[],owned=[];
 for(let i=0;i<visible.count;i++){
  const popup=read(visible.array+16+i*4);if(!derived(popup,'Popup'))return fail('visible_popup_unavailable');
  owned.push(popup);
  const isActive=componentActive(popup);if(isActive===null)return fail('visible_popup_unavailable');
  if(!isActive)continue;
  let touch;try{const value=fn(slots.touch)(popup,0);touch=value===1?true:value===0?false:null;}catch{return fail('touch_unavailable');}
  if(touch===null)return fail('touch_unavailable');
  if(touch)active.push(popup);
 }
 if(!visible.count)return {...common,panel:'none',navigation_action:'clear',popup_state:0};
 if(active.length>1)return fail('visible_popup_ambiguous');
 if(!active.length)return {...common,panel:'transition',navigation_action:'wait',popup_state:0};
 const popup=active[0],canClose=bool(popup,18),popupState=read(popup+56);
 if(canClose===null||!Number.isInteger(popupState)||popupState>4)return fail('popup_state_unavailable');
 const output={...common,popup_index:owned.indexOf(popup),popup_state:popupState,can_close:canClose,panel:'unsupported',navigation_action:'blocked'};
 const popupClass=read(popup),className=read(popupClass+8);
 for(const [name,offset] of Object.entries(popupNames))if(className===base+offset&&exact(className,name)&&exact(read(popupClass+12),'')){output.public_popup_class=name;break;}
 const ordinaryMenus=['ResearchPopup','ResearchProgressPopup','AdBoostPopup','WatchAdsPopup','ShopPopup'];
 if(ordinaryMenus.some(name=>recognized(popup,name))){
  if(!owned.every(item=>ordinaryMenus.some(name=>recognized(item,name))))return {...output,navigation_action:'blocked'};
  return {...output,panel:'ordinary_menu',navigation_action:'clear',retained_menu:true};
 }
 let receiver=popup,slot=null,releaseSlot=null;
 const buttons=p=>{
  if(!recognized(p,'RewardPopupButtonsView'))return null;
  const rows=list(read(p+16),12);if(!rows||!rows.count)return null;
  const found={};
  for(let i=0;i<rows.count;i++){
   const row=read(rows.array+16+i*4);if(!recognized(row,'ButtonToGameObject'))return null;
   const kind=read(row+8),object=read(row+12),isActive=gameActive(object);
   if(kind>6||Object.hasOwn(found,kind)||isActive===null)return null;
   found[kind]=isActive;
  }
  return found;
 };
 if(recognized(popup,'RewardPopup')){
  const tutorial=bool(popup,field('RewardPopup','<IsTutorialFlow>k__BackingField')),buyMore=bool(popup,field('RewardPopup','<BuyMoreClicked>k__BackingField')),waiting=bool(popup,field('RewardPopup','_mustWaitShowRoutine'));
  if(tutorial===null||buyMore===null||waiting===null)return fail('reward_state_unavailable');
  output.panel='reward';output.animation_waiting=waiting;output.continue_callback_absent=read(popup+field('RewardPopup','OnDismiss'))===0;
  if(tutorial||buyMore)return {...output,navigation_action:'blocked'};
  const controller=read(popup+field('RewardPopup','_openAnimationController'));
  if(controller){
   if(!recognized(controller,'RewardPopupOpeningAnimationController'))return fail('animation_controller_unavailable');
   const paused=bool(controller,28),held=bool(controller,29);
   if(paused===null||held===null)return fail('animation_state_unavailable');
   output.animation_paused=paused;
   const unpause=read(controller+16);
   if(paused&&!held&&recognized(unpause,'tk2dUIItem')&&componentActive(unpause)===true){
    output.navigation_action='reveal';receiver=controller;slot=slots.reveal_press;releaseSlot=slots.reveal_release;
   }
  }
  if(slot===null){
   const map=buttons(read(popup+field('RewardPopup','_buttonsView')));if(!map)return {...output,navigation_action:'wait'};
   output.close_offered=map[2]===true;output.continue_offered=map[5]===true;
   if(waiting)return {...output,navigation_action:'wait'};
   if(canClose&&map[2]===true){output.navigation_action='close';slot=slots.reward_close;}
   else if(map[5]===true&&output.continue_callback_absent){output.navigation_action='continue';slot=slots.reward_continue;}
  }
 }else if(recognized(popup,'RewardChoicePopup')){
  const data=read(popup+field('RewardChoicePopup','rewardData'));if(!recognized(data,'ServiceRewardChoiceData'))return fail('choice_state_unavailable');
  const type=read(data+12);if(type>1000000)return fail('choice_state_unavailable');
  output.panel='choice';output.choice_type=type;
  const map=buttons(read(popup+field('RewardChoicePopup','_buttonsView')));if(!map)return {...output,navigation_action:'wait'};
  output.close_offered=map[2]===true;output.continue_offered=map[5]===true;
  if(canClose&&map[2]===true){output.navigation_action='close';slot=slots.choice_close;}
  else if(map[5]===true){output.navigation_action='continue';slot=slots.choice_continue;}
  else output.navigation_action='choice';
 }else if(recognized(popup,'RewardListPopup')){
  const button=read(popup+100);output.panel='reward_list';
  if(recognized(button,'tk2dUIItem')&&componentActive(button)===true&&canClose){output.navigation_action='close';slot=slots.hide;}
 }else if(recognized(popup,'QuestCompletePopup')){
  output.panel='quest_complete';output.navigation_action='blocked';
  const quest=read(popup+116),clicked=bool(popup,124),auto=bool(popup,125),box=read(popup+112);
  if(!recognized(quest,'ServiceAchievementData')||read(classes.ServiceAchievementData+44)!==classes.ServiceDeepLinkData||clicked===null||auto===null)return fail('quest_state_unavailable');
  const id=read(quest+8),progress=read(quest+68),maximum=read(quest+72),status=read(quest+76);
  if(id<1||id>2147483647||progress>2147483647||maximum<1||maximum>2147483647||status>2)return fail('quest_state_unavailable');
  output.quest_id=id;output.quest_progress=progress;output.quest_max_progress=maximum;output.quest_status=status;
  output.quest_box_active=recognized(box,'SkeletonAnimation')&&componentActive(box)===true;
  output.quest_loading=gameActive(read(popup+108));output.quest_box_clicked=clicked;output.quest_auto_complete=auto;output.quest_received_display_verified=false;
  if(output.quest_loading===null)return fail('quest_state_unavailable');
  if(output.quest_loading)return {...output,navigation_action:'wait'};
  const delegate=read(popup+84),method=read(binding.quest_received_method),capture=read(delegate+16),reward=read(capture+12);
  output.quest_received_display_verified=(valid(delegate,64)&&derived(delegate,'Delegate')&&derived(delegate,'MulticastDelegate')
   &&classMatches(read(delegate),'Action`1')&&read(delegate+60)===0&&read(delegate+8)===slots.quest_received
   &&valid(method,24)&&read(delegate+20)===method&&read(method)===slots.quest_received
   &&valid(capture,16)&&classMatches(read(capture),'<>c__DisplayClass15_0')&&read(capture+8)===popup
   &&recognized(reward,'ServiceRewardData')&&bool(reward,8)===true&&read(popup+80)===0&&read(popup+120)===0);
  // The installed b__2 only calls ShowRewards(captured received rewards).
  // CompleteAchievement is never invoked by this helper.
  if(canClose&&output.quest_box_active&&!clicked&&!auto&&output.quest_received_display_verified){output.navigation_action='reveal';slot=slots.hide;}
  else if(clicked||(!output.quest_box_active&&!auto))output.navigation_action='wait';
 }else if(recognized(popup,'SaleLastChanceOfferPopup')){
  output.panel='promotion';output.hide_callbacks_absent=read(popup+80)===0&&read(popup+84)===0;
  // Public Awake binds this owned Close item to inherited Popup.Hide. Buy is
  // the distinct item at164 and is never inspected or invoked here.
  const button=read(popup+100);
  output.close_offered=recognized(button,'tk2dUIItem')&&componentActive(button)===true;
  if(canClose&&output.close_offered&&output.hide_callbacks_absent){output.navigation_action='close';slot=slots.hide;}
 }
 if(request.operation==='inspect')return output;
 const expected=request.expected;
 if(!expected||slot===null||['panel','navigation_action','stack_revision','popup_state','popup_index','visible_count','public_popup_class'].some(key=>expected[key]!==output[key])||(output.panel==='quest_complete'&&expected.quest_id!==output.quest_id))return fail('popup_changed');
 if(m.HEAPU32!==h||m.HEAPU8!==b||read(statics+8)!==manager||read(manager+52)!==visiblePointer||read(popup)!==popupClass||componentActive(popup)!==true)return fail('popup_changed');
 try{fn(slot)(receiver,0);if(releaseSlot!==null)fn(releaseSlot)(receiver,0);}catch{return fail('navigation_unknown');}
 return {...common,panel:output.panel,navigation_action:output.navigation_action,action_sent:true};
})()'''


def _prepared(prep):
    target = prep.get("target") if isinstance(prep, Mapping) else None
    return (isinstance(prep, Mapping) and prep.get("ok") is True
            and isinstance(target, Mapping) and isinstance(target.get("webSocketDebuggerUrl"), str)
            and bool(target["webSocketDebuggerUrl"]))


def _expression(operation, expected=None):
    if operation not in {"inspect", "advance"}:
        raise ValueError("Unsupported startup navigation operation.")
    request = {"operation": operation}
    if operation == "advance":
        if not _valid_observation(expected) or expected["navigation_action"] not in {"close", "continue", "reveal"}:
            raise ValueError("Unverified startup navigation action.")
        request["expected"] = {key: expected[key] for key in
                               ("panel", "navigation_action", "stack_revision", "popup_state",
                                "popup_index", "visible_count", "public_popup_class")}
        if expected["panel"] == "quest_complete":
            request["expected"]["quest_id"] = expected["quest_id"]
    return (_NATIVE_JS.replace("__REQUEST__", json.dumps(request))
            .replace("__BUILDS__", json.dumps(PUBLIC_BUILD_BINDINGS)))


def _evaluate(prep, operation, expected=None):
    from .native_research_ui import _cdp_native
    response = _cdp_native(prep["target"]["webSocketDebuggerUrl"], _expression(operation, expected))
    if response.get("exceptionDetails"):
        raise RuntimeError("The startup UI result is unavailable.")
    result = (response.get("result") or {}).get("value")
    if not isinstance(result, dict):
        raise RuntimeError("The startup UI result is unavailable.")
    return result


def _valid_observation(value):
    if not (isinstance(value, Mapping) and value.get("ok") is True
            and all(isinstance(value.get(key), int) and not isinstance(value[key], bool)
                    and 0 <= value[key] <= limit for key, limit in
                    (("stack_revision", 2147483647), ("visible_count", 16), ("popup_state", 4)))):
        return False
    panel, action = value.get("panel"), value.get("navigation_action")
    if panel == "loading":
        return (action == "wait" and value.get("runtime_loading") is True
                and isinstance(value.get("verified_public_build"), bool)
                and value["visible_count"] == 0)
    if value.get("verified_public_build") is not True:
        return False
    actions = {"none": {"clear"}, "ordinary_menu": {"clear"},
               "transition": {"wait"}, "unsupported": {"blocked"},
               "reward": {"wait", "blocked", "close", "continue", "reveal"},
               "choice": {"wait", "blocked", "choice", "close", "continue"},
               "reward_list": {"blocked", "close"}, "quest_complete": {"blocked", "wait", "reveal"},
               "promotion": {"blocked", "close"}}
    if action not in actions.get(panel, set()):
        return False
    if "public_popup_class" in value and (not isinstance(value["public_popup_class"], str)
                                         or value["public_popup_class"] not in PUBLIC_POPUP_NAMES):
        return False
    if panel not in {"none", "transition"} and not (
            isinstance(value.get("popup_index"), int) and not isinstance(value["popup_index"], bool)
            and 0 <= value["popup_index"] < value["visible_count"]
            and isinstance(value.get("public_popup_class"), str)):
        return False
    for key in ("can_close", "animation_waiting", "animation_paused", "continue_callback_absent",
                "close_offered", "continue_offered", "hide_callbacks_absent", "retained_menu"):
        if key in value and not isinstance(value[key], bool):
            return False
    if "choice_type" in value and (not isinstance(value["choice_type"], int)
                                  or isinstance(value["choice_type"], bool) or not 0 <= value["choice_type"] <= 1000000):
        return False
    if panel == "quest_complete" and action != "blocked":
        if not all(isinstance(value.get(key), int) and not isinstance(value[key], bool)
                   and low <= value[key] <= high for key, low, high in (
                   ("quest_id", 1, 2147483647), ("quest_progress", 0, 2147483647),
                   ("quest_max_progress", 1, 2147483647), ("quest_status", 0, 2))):
            return False
        if not all(isinstance(value.get(key), bool) for key in (
                "quest_box_active", "quest_loading", "quest_box_clicked", "quest_auto_complete",
                "quest_received_display_verified")):
            return False
    if panel == "ordinary_menu":
        return (value.get("retained_menu") is True and value["visible_count"] > 0
                and value.get("public_popup_class") in {
                    "ResearchPopup", "ResearchProgressPopup", "AdBoostPopup", "WatchAdsPopup", "ShopPopup"})
    return True


def inspect_startup_popup(prep):
    """Return safe public navigation metadata; never invoke a UI action."""
    failure = {"ok": False, "reason": "startup_ui_unavailable", "verified_public_build": False}
    if not _prepared(prep):
        return {**failure, "reason": "browser_not_prepared"}
    try:
        raw = _evaluate(prep, "inspect")
    except Exception:
        return failure
    if not _valid_observation(raw):
        return failure
    allowed = {"ok", "verified_public_build", "panel", "navigation_action", "stack_revision",
               "visible_count", "popup_index", "popup_state", "can_close", "animation_waiting", "animation_paused",
               "continue_callback_absent", "close_offered", "continue_offered", "choice_type", "public_popup_class",
               "hide_callbacks_absent", "runtime_loading", "retained_menu"}
    allowed.update({"quest_id", "quest_progress", "quest_max_progress", "quest_status",
                    "quest_box_active", "quest_loading", "quest_box_clicked", "quest_auto_complete",
                    "quest_received_display_verified"})
    if "public_popup_class" in raw and raw["public_popup_class"] not in PUBLIC_POPUP_NAMES:
        return failure
    return {key: value for key, value in raw.items() if key in allowed}


def advance_startup_popup(prep, action):
    """Recheck one offered native control, then invoke its ordinary handler once.

    This does not authorize a server choice, claim, purchase, ad or callback.
    The caller must reconcile an uncertain navigation result rather than resend.
    """
    failure = {"ok": False, "reason": "startup_navigation_unavailable", "action_sent": False}
    if not _prepared(prep) or not _valid_observation(action):
        return failure
    try:
        raw = _evaluate(prep, "advance", action)
    except Exception:
        return {**failure, "reason": "startup_navigation_unknown", "unknown_outcome": True}
    if (raw.get("ok") is not True or raw.get("verified_public_build") is not True
            or raw.get("action_sent") is not True):
        return {**failure, "reason": "startup_navigation_unverified", "unknown_outcome": True}
    return {"ok": True, "verified_public_build": True, "action_sent": True}


def _integer(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    return None


def _unavailable():
    raise CommandUnavailable(_MESSAGE) from None


def _fresh(service):
    try:
        context = getattr(service, "context", None)
        if context is not None and callable(getattr(context, "invalidate", None)):
            context.invalidate()
        current = service.snapshot(refresh=True)
    except Exception:
        _unavailable()
    if not isinstance(current, dict) or api_rejected(current):
        _unavailable()
    return current


def _gems(current):
    found = {}
    for section, key in _GEMS:
        container = current.get(section)
        if not isinstance(container, Mapping) or key not in container:
            continue
        number = _integer(container[key])
        if number is None or number < 0:
            _unavailable()
        found[(section, key)] = number
    if not found:
        _unavailable()
    return found


def _no_gem_spend(before, after):
    fresh = _gems(after)
    if any(key not in fresh or fresh[key] < amount for key, amount in before.items()):
        _unavailable()


def _choice_groups(current, catalog):
    if "active_choices" not in current or not isinstance(current["active_choices"], (dict, list)):
        _unavailable()
    groups = current["active_choices"]
    groups = list(groups.values()) if isinstance(groups, dict) else groups
    if len(groups) > MAX_CHOICES:
        _unavailable()
    parsed = extract_item_choices(current, catalog)
    selected = []
    types = set()
    for index, group in enumerate(groups):
        if not isinstance(group, Mapping):
            _unavailable()
        kind = _integer(group.get("type"))
        options = group.get("options")
        options = list(options.values()) if isinstance(options, dict) else options
        if kind is None or not 0 <= kind <= 1000000 or kind in types or not isinstance(options, list) or not 1 <= len(options) <= 64:
            _unavailable()
        ids = [_integer(option.get("id")) if isinstance(option, Mapping) else None for option in options]
        if any(id_ is None or id_ <= 0 for id_ in ids) or len(set(ids)) != len(ids):
            _unavailable()
        alternatives = [row for row in parsed if row["group"] == index]
        if len(alternatives) != len(options):
            _unavailable()
        try:
            choice = choose_item_reward(alternatives)
        except Exception:
            _unavailable()
        selected.append(choice)
        types.add(kind)
    return selected


def _quest_postclaim_ready(service, current, observed):
    """Allow only the shown already-received quest display, never claim a quest.

    The ordinary native game starts its own completion routine as the quest
    popup appears. While that routine is running this helper waits. Its offered
    box is navigable only once the exact installed received-reward closure and
    fresh completed/removed server quest have both been proven.
    """
    if (observed.get("quest_received_display_verified") is not True
            or observed.get("quest_box_active") is not True
            or any(observed.get(key) is not False for key in
                   ("quest_loading", "quest_box_clicked", "quest_auto_complete"))):
        _unavailable()
    quests = current.get("user_achievements")
    if not isinstance(quests, (dict, list)):
        _unavailable()
    try:
        now = service.clock()
    except Exception:
        _unavailable()
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now) or now <= 0:
        _unavailable()
    from .reward_options import claim_plan
    qid = observed["quest_id"]
    if any(row["payload"]["achievement_id"] == qid for row in claim_plan(current, ("quests",), int(now))):
        _unavailable()
    matching = []
    visited = 0

    def walk(value, depth=0):
        nonlocal visited
        visited += 1
        if depth > 12 or visited > 10000:
            _unavailable()
        if isinstance(value, list):
            for child in value:
                walk(child, depth + 1)
        elif isinstance(value, dict):
            if _integer(value.get("id")) == qid:
                matching.append(value)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child, depth + 1)

    walk(quests)
    if len(matching) > 1:
        _unavailable()
    if matching:
        quest = matching[0]
        progress, maximum = _integer(quest.get("progress")), _integer(quest.get("max_progress"))
        if (_integer(quest.get("status")) != 2 or progress is None or maximum is None
                or maximum <= 0 or progress < maximum):
            _unavailable()


def settle_startup_popups(service, prep, *, should_stop=None, log=None,
                          wait_seconds=60, max_actions=MAX_ACTIONS, max_choices=MAX_CHOICES):
    """Settle current startup rewards, never open an item or start an ad.

    Choices are confirmed once from fresh complete options with existing Rogu →
    Character → Item/PC priority. Each confirmation and ordinary navigation is
    followed by fresh server state. Unchanged/uncertain controls are not replayed.
    All exposed Gem mirrors are protected from decreases, including on failure.
    """
    stopped = should_stop or (lambda: False)
    if (not _prepared(prep) or isinstance(wait_seconds, bool)
            or not isinstance(wait_seconds, (int, float)) or not math.isfinite(wait_seconds)
            or not 1 <= wait_seconds <= MAX_WAIT_SECONDS
            or any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= limit
                   for value, limit in ((max_actions, MAX_ACTIONS), (max_choices, MAX_CHOICES)))):
        _unavailable()
    deadline = time.monotonic() + wait_seconds
    actions = choices = 0
    sent = set()
    previous = None
    catalog = None
    current = None
    baseline = None
    while time.monotonic() < deadline:
        if stopped():
            _unavailable()
        if current is None:
            current = _fresh(service)
        if baseline is None:
            baseline = _gems(current)
        _no_gem_spend(baseline, current)
        if catalog is None:
            try:
                catalog = service.catalog("card")
            except Exception:
                _unavailable()
        selected = _choice_groups(current, catalog)
        if selected:
            if choices >= max_choices or stopped():
                _unavailable()
            chosen = selected[0]
            before = _gems(current)
            uncertain = False
            try:
                response = service.call("confirmUserChoice", {"type": chosen["choice_type"], "choice": chosen["id"]})
            except Exception:
                uncertain = True
                response = None
            current = _fresh(service)
            _no_gem_spend(before, current)
            remaining = _choice_groups(current, catalog)
            if any(row["choice_type"] == chosen["choice_type"] for row in remaining):
                _unavailable()
            if not uncertain and not item_choice_confirmed(response, chosen["choice_type"]):
                _unavailable()
            choices += 1
            previous = None
            if log:
                log("Startup rewards: confirmed one choice with Rogu → Characters → Items / Power Cards priority.")
            continue
        observed = inspect_startup_popup(prep)
        if not _valid_observation(observed):
            _unavailable()
        action = observed["navigation_action"]
        if action == "clear":
            if not ((observed["panel"] == "none" and observed["visible_count"] == 0)
                    or observed["panel"] == "ordinary_menu"):
                _unavailable()
            # A second fresh read protects a choice queued while navigation ended.
            current = _fresh(service)
            _no_gem_spend(baseline, current)
            if _choice_groups(current, catalog):
                previous = None
                continue
            return {"ok": True, "verified": True, "verified_public_build": True,
                    "navigation_actions": actions, "resolved_choices": choices,
                    "gem_spend": 0, "playback_started": False,
                    "retained_menu": observed.get("public_popup_class") if observed["panel"] == "ordinary_menu" else None}
        if action in {"blocked", "choice"}:
            _unavailable()
        key = tuple(observed.get(key) for key in ("stack_revision", "panel", "navigation_action", "popup_state",
                                                "popup_index", "visible_count", "public_popup_class", "quest_id"))
        if action == "wait" or key in sent or key != previous:
            previous = key
            time.sleep(.25)
            # Server state must be refreshed before any later visible action.
            current = None
            continue
        if actions >= max_actions or stopped():
            _unavailable()
        before = _gems(current)
        if observed["panel"] == "quest_complete":
            _quest_postclaim_ready(service, current, observed)
        sent.add(key)
        outcome = advance_startup_popup(prep, observed)
        actions += 1
        current = _fresh(service)
        _no_gem_spend(before, current)
        if outcome.get("ok") is not True or outcome.get("action_sent") is not True:
            _unavailable()
        previous = None
        if log:
            log("Startup rewards: advanced one verified reward display.")
        time.sleep(.25)
    _unavailable()
