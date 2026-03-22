# KG Use Case Draft v2.2
## Use Case 1: CAC Spike Driven by Mobile Landing-Page Conversion Failure in a Top-Spend Meta Ad Set

```yaml
rule_id: rule_cac_spike_meta_lp_mobile_v1

layer_1:
  domain: marketing_operations
  modules:
    primary: acquisition_efficiency
    secondary: conversion_merchandising
  business_model: cpg_dtc_omnichannel
  vertical: food_beverage_coffee
  channels:
    - meta_ads
    - google_ads
    - ga4
    - ecommerce_platform
  entities:
    - merchant
    - campaign
    - ad_set
    - landing_page
    - device_category
    - browser
    - operating_system
    - product_bundle

layer_2:
  context:
    merchant_type: omnichannel CPG coffee brand
    applies_broadly_to:
      - recurring consumable CPG
      - food and beverage brands
      - coffee brands
    products_in_scope:
      - Espresso Roast / Straight Black bundle landing page
    notes:
      - Meta is the primary demand-generation channel
      - Google is more demand-capture oriented

  problem_pattern:
    name: spend_customer_divergence_cac_spike
    business_risk:
      - reduced new customer growth
      - worse CAC efficiency
      - worse payback period
      - reduced contribution margin
    observations:
      before_issue:
        period: 4 days before 2026-03-01
        spend_per_day: 1700.70
        customers_per_day: 30
        cac: 56.22
      during_issue:
        period: 2026-03-01 through 2026-03-05
        spend_per_day: 2920.61
        customers_per_day: 41
        cac: 72.11
      after_issue:
        period: 4 days after 2026-03-05
        spend_per_day: 2380.48
        customers_per_day: 40
        cac: 60.27

  trigger:
    all:
      - metric: cac_7d
        op: ">="
        value: 1.15
        relative_to: prior_7d_same_dow_baseline
      - metric: cac_spike_duration_days
        op: ">="
        value: 4
      - metric: spend_growth_rate
        op: ">"
        value: customer_growth_rate
    description: |
      Trigger when CAC rises 15%+ versus the prior 7-day baseline, the rise persists for
      4+ days, and spend growth materially outpaces customer growth.

  checks:
    - check: identify_top_spend_channel
      why: Highest-spend channel is the most likely contributor to total CAC movement.
      confirm_if: One channel represents majority share of spend and shows CPA deterioration.
      result_in_case: Meta represented ~82% of spend.

    - check: compare_upper_funnel_metrics
      why: If CPC/CPM are stable but CAC worsens, the problem is more likely post-click than auction-driven.
      confirm_if: CPC, CPM, and cost per checkout initiated do not move enough to explain the CAC delta.
      result_in_case: Meta CPAs rose without meaningful supporting rise in CPCs, CPMs, or cost per checkout initiated.

    - check: isolate_campaign_and_ad_set_concentration
      why: A broad account issue behaves differently from a concentrated campaign/ad set issue.
      confirm_if: Incremental spend and CPA deterioration are concentrated in a specific campaign or ad set.
      result_in_case:
        campaign: testing_campaign
        ad_sets:
          - new_product_test
          - bundle_ad_set
        bundle_ad_set:
          cpa_before: 41
          cpa_during: 58
          spend_before: 750
          spend_during: 1250

    - check: locate_funnel_break
      why: Funnel-stage diagnosis narrows whether the issue is traffic quality, page experience, or checkout friction.
      confirm_if: CVR deteriorates while upper-funnel metrics remain relatively stable.
      result_in_case: Main deterioration occurred in CVR.

    - check: segment_ga4_by_device_browser_os
      why: Technical landing-page issues often appear in specific device/browser environments.
      confirm_if: Mobile / browser / OS segments show disproportionate conversion decline.
      result_in_case:
        mobile_cvr_change: -17%
        desktop_tablet_change: flat
        weak_segments:
          - android
          - android_webview
          - samsung_devices
          - google_devices

    - check: manual_device_validation
      why: Technical issues should be confirmed in the live user environment before over-correcting spend.
      confirm_if: Page load or interaction issue is reproducible on relevant devices.
      result_in_case: Landing-page issue confirmed on relevant devices.

  because: |
    A CAC spike concentrated in Meta, without a proportional rise in CPC/CPM, combined with
    a CVR deterioration isolated to mobile/Android/WebView environments, strongly suggests a
    landing-page technical issue rather than broad media inefficiency or creative fatigue.

  root_cause:
    primary: mobile landing-page technical issue on Espresso Roast / Straight Black bundle page
    secondary: none identified as primary drivers
    confidence: high

  suggested_actions:
    - action_type: REDUCE_SPEND_ON_AFFECTED_AD_SET
      priority: 1
      recommendation: Reduce spend on the affected ad set by ~50% to stop the bleeding.
      preconditions:
        - affected_ad_set_is_identified
        - cvr_drop_is_material
      automation_level: semi_automated

    - action_type: VALIDATE_PAGE_ON_AFFECTED_DEVICE_ENVIRONMENTS
      priority: 2
      recommendation: Test the page on Android / WebView / Samsung / Google device environments.
      preconditions:
        - ga4_segment_data_shows_device_browser_issue
      automation_level: warn_only

    - action_type: REDIRECT_TRAFFIC_TO_ALTERNATE_EXPERIENCE
      priority: 3
      recommendation: Route traffic to an alternate landing-page flow not subject to the same issue.
      preconditions:
        - alternate_flow_exists
        - issue_is_confirmed
      automation_level: operator_only

  do_not_recommend:
    - TURN_OFF_AD_SET_COMPLETELY
  do_not_recommend_because: |
    The ad set had strong historical performance, so a full shutoff would overreact to what
    appears to be an underlying technical issue rather than a structurally bad ad set.

  constraints:
    hard_constraints:
      - sufficient sample size required for CVR diagnosis
      - do not over-infer from sparse device-model data
    warn_only_conditions:
      - root cause not yet manually validated
    assumptions:
      - CAC movement should roughly reconcile with CVR deterioration magnitude

  required_data_sources:
    minimum_viable:
      - meta_ads
      - ga4
    recommended:
      - google_ads
      - shopline_or_shopify_order_data

  outcome_expectation:
    observed_result:
      during_issue_cac: 72.11
      post_issue_cac: 60.27
      post_issue_spend_change_vs_during: -18%
      cac_improvement_vs_during: -16%
    before_vs_after_excluding_issue_days:
      spend_change: +40%
      customer_change: +31%
      cac_change: +7%
    business_effect:
      - CAC returned closer to acceptable range
      - payback improved materially
      - media efficiency improved meaningfully

  merchant_facing_recommendation: |
    Reduce spend on the affected bundle ad set by 50% and investigate Android / WebView landing-page
    performance immediately, because the CAC spike appears to be driven by a mobile conversion issue
    rather than broad media inefficiency.

  reusable_rule_sentence: |
    If CAC rises materially in a top-spend paid channel without a matching rise in CPC/CPM,
    investigate ad-set-level funnel deterioration and segment landing-page conversion by device,
    browser, and operating system to identify technical conversion failures.

  common_ai_failure_mode: |
    A weaker model will over-weight CPC/CPM movement, blame generic media inefficiency,
    or recommend refreshing creative before checking the landing page and device/browser segments.
```
