"""Heuristic denylists for entity resolution. Favor precision over recall
(docs/ARCHITECTURE.md) — better to leave a host unresolved than to merge it
into the wrong company, or worse, "resolve" a hyperscaler tenant IP into the
hyperscaler itself.

These lists are seeded from what we observed sampling the dataset; expected
to grow as Sprint 2 QA surfaces more hosting/CDN providers slipping through.
"""

# Shodan's `domains` field is already a root-domain extraction. These are
# domains OWNED BY hosting/cloud/CDN providers for their reverse-DNS zones —
# seeing one of these does not tell us who the tenant is.
SHARED_INFRA_DOMAIN_SUFFIXES = frozenset({
    "googleusercontent.com", "1e100.net", "gvt1.com", "withgoogle.com",
    "amazonaws.com", "awsglobalaccelerator.com",
    "azure.com", "azurewebsites.net", "cloudapp.net", "cloudapp.azure.com",
    "cloudflare.net", "cloudflare.com",
    "akamaitechnologies.com", "akadns.net", "akamaiedge.net", "akamai.net",
    "incapdns.net", "impervadns.net",
    "fastly.net", "fastlylb.net",
    "cloudfront.net",
    "digitalocean.com", "digitaloceanspaces.com",
    "linode.com", "linodeusercontent.com", "linodeobjects.com",
    "vultr.com", "vultrusercontent.com", "vultrobjects.com",
    "ovh.net", "ovh.com", "ovhcloud.com",
    "scaleway.com",
    "hetzner.com", "hetzner.cloud", "your-server.de",
    "oraclecloud.com",
    "aliyuncs.com", "alibabacloud.com",
    "tencentcloudapi.com", "myqcloud.com",
    "herokuapp.com", "heroku.com",
    "netlify.app", "vercel.app", "github.io", "workers.dev", "pages.dev",
    "fly.dev", "flyio.net", "fly.io",
    "render.com", "onrender.com",
    "contabo.net", "contabo.host",
    "softlayer.com", "rackspace.com",
})

# Major/well-known brand domains. At the scale of a Fortune-500-class
# company, a resolved domain is never a genuine sales lead - it's either
# real infrastructure we should have caught above, or (as found via QA - an
# "amazon.com" entry whose org was actually NTT America, flagged c2) a
# spoofed reverse-DNS PTR on unrelated/malicious infrastructure impersonating
# the brand. PTR records are controlled by whoever owns the IP block, not by
# the real domain owner, so they're trivially spoofable.
BRAND_DOMAINS = frozenset({
    "amazon.com", "google.com", "microsoft.com", "apple.com", "facebook.com",
    "meta.com", "netflix.com", "paypal.com", "ebay.com",
})

# Reserved/placeholder hostnames (RFC 2606, or just common device defaults)
# that show up as `domains` values on misconfigured devices across many
# unrelated networks. Found via QA: "localhost." alone had aggregated 2,375
# unrelated hosts into one fake "company".
RESERVED_PLACEHOLDER_DOMAINS = frozenset({
    "localhost", "localhost.", "localdomain", "invalid",
    "example.com", "example.net", "example.org", "local",
})

NON_TARGET_DOMAINS = SHARED_INFRA_DOMAIN_SUFFIXES | BRAND_DOMAINS | RESERVED_PLACEHOLDER_DOMAINS

# Substring match (lowercased) against org/isp for the Tier-B fallback and
# for rejecting Tier-A domain matches whose owning org is itself the infra
# provider (e.g. an Incapsula IP whose PTR happens to resolve under a
# customer-looking name).
HYPERSCALER_ORG_KEYWORDS = frozenset({
    "google", "amazon", "microsoft corporation", "microsoft azure", "azure",
    "cloudflare", "incapsula", "imperva", "akamai", "alibaba", "aliyun",
    "tencent cloud", "oracle cloud", "digitalocean", "linode", "akamai technologies",
    "vultr", "the constant company", "ovh", "scaleway", "hetzner", "fastly",
    "fly.io", "heroku", "netlify", "vercel", "rackspace", "contabo",
    "softlayer", "ibm cloud", "leaseweb", "choopa", "quadranet",
    "colocrossing", "psychz", "level 3 parent", "cdn77",
})

# Substring match against org/isp for rejecting the Tier-B org-name fallback:
# generic consumer/telecom ISPs whose "org" is the carrier, not the business
# operating the flagged device (residential/mobile broadband pools).
GENERIC_ISP_KEYWORDS = frozenset({
    "telecom", "telekom", "telefonica", "telstra", "broadband", "wireless",
    "cellular", "mobile", "cable", "internet service", " isp", "fiber",
    "gpon", "residential",
})


def is_shared_infra_domain(domain: str) -> bool:
    if not domain:
        return False
    d = domain.lower()
    return any(d == s or d.endswith("." + s) for s in SHARED_INFRA_DOMAIN_SUFFIXES)


def is_hyperscaler_org(org: str) -> bool:
    if not org:
        return False
    o = org.lower()
    return any(k in o for k in HYPERSCALER_ORG_KEYWORDS)


def is_generic_isp_org(org: str) -> bool:
    if not org:
        return False
    o = org.lower()
    return any(k in o for k in GENERIC_ISP_KEYWORDS)
