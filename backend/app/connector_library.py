"""Ready-made connector manifests for well-known REST APIs.

Every entry is written from public API documentation and has NEVER been run against the live service by this project.
Treat each one as a starting point: try it with a test account, and fix the manifest in Settings if the API differs.
"""


def _op(label, path, method="GET", query=None, body=None, required=(), result="", example=None):
    op = {"label": label, "method": method, "path": path, "required": list(required), "result": result, "example": example or {}}
    if query: op["query"] = query
    if body is not None: op["body"] = body
    return op


def _entry(key, label, base, auth, ops, about):
    return {"id": key, "about": about, "manifest": {"label": label, "base_url": base, "auth": auth, "operations": ops}}


NONE = {"type": "none"}
BEARER = {"type": "bearer"}

LIBRARY = [
    _entry("jsonplaceholder", "JSONPlaceholder (practice API)", "https://jsonplaceholder.typicode.com", NONE, {
        "list_posts": _op("List posts", "/posts", query={"userId": "{user_id}"}, example={}),
        "get_post": _op("Get a post", "/posts/{id}", required=["id"], example={"id": 1}),
        "create_post": _op("Create a post (fake)", "/posts", "POST", body={"title": "{title}", "body": "{body}", "userId": "{user_id}"}, required=["title"], example={"title": "Hello", "body": "World", "user_id": 1}),
        "list_users": _op("List users", "/users"),
    }, "A free fake API for trying workflows. No sign-in."),
    _entry("openmeteo", "Open-Meteo weather", "https://api.open-meteo.com/v1", NONE, {
        "forecast": _op("Current weather and daily forecast", "/forecast", query={"latitude": "{latitude}", "longitude": "{longitude}", "current_weather": "true", "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum", "timezone": "auto"}, required=["latitude", "longitude"], example={"latitude": 48.85, "longitude": 2.35}),
    }, "Free weather forecasts, no key for non-commercial use."),
    _entry("openweathermap", "OpenWeatherMap", "https://api.openweathermap.org/data/2.5", {"type": "query", "name": "appid"}, {
        "current": _op("Current weather for a city", "/weather", query={"q": "{city}", "units": "{units}"}, required=["city"], example={"city": "London", "units": "metric"}),
        "forecast": _op("5-day forecast for a city", "/forecast", query={"q": "{city}", "units": "{units}"}, required=["city"], example={"city": "London", "units": "metric"}),
    }, "Credential: your API key."),
    _entry("nasa_apod", "NASA Astronomy Picture of the Day", "https://api.nasa.gov/planetary", {"type": "query", "name": "api_key"}, {
        "today": _op("Picture of the day", "/apod", query={"date": "{date}"}, example={}),
    }, "Credential: a free key from api.nasa.gov (DEMO_KEY works with low limits)."),
    _entry("restcountries", "REST Countries", "https://restcountries.com/v3.1", NONE, {
        "by_name": _op("Find a country by name", "/name/{name}", query={"fields": "name,capital,region,population,currencies,languages,flags"}, required=["name"], example={"name": "France"}),
        "by_code": _op("Find a country by code", "/alpha/{code}", required=["code"], example={"code": "FR"}),
    }, "Country facts. No sign-in."),
    _entry("frankfurter", "Frankfurter exchange rates", "https://api.frankfurter.app", NONE, {
        "latest": _op("Latest rates", "/latest", query={"from": "{from}", "to": "{to}", "amount": "{amount}"}, example={"from": "EUR", "to": "USD", "amount": 100}),
        "historical": _op("Rates on a date", "/{date}", query={"from": "{from}", "to": "{to}"}, required=["date"], example={"date": "2024-01-02", "from": "EUR", "to": "USD"}),
    }, "European Central Bank rates. No sign-in."),
    _entry("pokeapi", "PokeAPI", "https://pokeapi.co/api/v2", NONE, {
        "pokemon": _op("Get a Pokémon", "/pokemon/{name}", required=["name"], example={"name": "pikachu"}),
    }, "A free practice API. No sign-in."),
    _entry("hackernews", "Hacker News", "https://hacker-news.firebaseio.com/v0", NONE, {
        "top_stories": _op("Top story ids", "/topstories.json"),
        "new_stories": _op("Newest story ids", "/newstories.json"),
        "item": _op("Get an item", "/item/{id}.json", required=["id"], example={"id": 8863}),
    }, "Official read-only API. No sign-in. Lists hold ids; use Get an item for the details."),
    _entry("devto", "DEV Community", "https://dev.to/api", {"type": "header", "name": "api-key"}, {
        "articles": _op("List articles", "/articles", query={"tag": "{tag}", "per_page": "{per_page}"}, example={"tag": "python", "per_page": 10}),
        "create_article": _op("Publish an article (draft)", "/articles", "POST", body={"article": {"title": "{title}", "body_markdown": "{body_markdown}", "published": False, "tags": ["{tag}"]}}, required=["title", "body_markdown"], example={"title": "My post", "body_markdown": "Hello", "tag": "automation"}),
    }, "Credential: a DEV API key (needed for publishing, reading is public)."),
    _entry("giphy", "GIPHY", "https://api.giphy.com/v1/gifs", {"type": "query", "name": "api_key"}, {
        "search": _op("Search GIFs", "/search", query={"q": "{query}", "limit": "{limit}"}, required=["query"], result="data", example={"query": "cats", "limit": 5}),
        "random": _op("Random GIF", "/random", query={"tag": "{tag}"}, result="data", example={"tag": "celebrate"}),
    }, "Credential: your GIPHY API key."),
    _entry("unsplash", "Unsplash", "https://api.unsplash.com", {"type": "header", "name": "Authorization"}, {
        "search_photos": _op("Search photos", "/search/photos", query={"query": "{query}", "per_page": "{per_page}"}, required=["query"], result="results", example={"query": "mountains", "per_page": 5}),
        "random_photo": _op("Random photo", "/photos/random", query={"query": "{query}"}, example={"query": "nature"}),
    }, "Credential value must be the whole header: Client-ID YOUR_ACCESS_KEY"),
    _entry("ipinfo", "IPinfo", "https://ipinfo.io", {"type": "query", "name": "token"}, {
        "lookup": _op("Look up an IP address", "/{ip}/json", required=["ip"], example={"ip": "8.8.8.8"}),
    }, "Credential: your IPinfo token."),
    _entry("calendly", "Calendly", "https://api.calendly.com", BEARER, {
        "me": _op("Who am I", "/users/me", result="resource"),
        "scheduled_events": _op("List scheduled events", "/scheduled_events", query={"user": "{user_uri}", "status": "{status}", "count": "{count}"}, required=["user_uri"], result="collection", example={"user_uri": "https://api.calendly.com/users/XXXX", "status": "active", "count": 20}),
    }, "Credential: a personal access token. Use Who am I first to find your user address."),
    _entry("typeform", "Typeform", "https://api.typeform.com", BEARER, {
        "forms": _op("List forms", "/forms", query={"page_size": "{page_size}"}, result="items", example={"page_size": 20}),
        "responses": _op("List responses of a form", "/forms/{form_id}/responses", query={"page_size": "{page_size}", "since": "{since}"}, required=["form_id"], result="items", example={"form_id": "abc123", "page_size": 25}),
    }, "Credential: a personal access token."),
    _entry("clockify", "Clockify", "https://api.clockify.me/api/v1", {"type": "header", "name": "X-Api-Key"}, {
        "user": _op("Who am I", "/user"),
        "workspaces": _op("List workspaces", "/workspaces"),
        "projects": _op("List projects", "/workspaces/{workspace_id}/projects", required=["workspace_id"], example={"workspace_id": "WORKSPACE_ID"}),
        "add_time_entry": _op("Add a time entry", "/workspaces/{workspace_id}/time-entries", "POST", body={"start": "{start}", "end": "{end}", "description": "{description}", "projectId": "{project_id}"}, required=["workspace_id", "start"], example={"workspace_id": "WORKSPACE_ID", "start": "2025-01-01T09:00:00Z", "end": "2025-01-01T10:00:00Z", "description": "Work"}),
    }, "Credential: your Clockify API key."),
    _entry("vercel", "Vercel", "https://api.vercel.com", BEARER, {
        "projects": _op("List projects", "/v9/projects", result="projects", example={}),
        "deployments": _op("List deployments", "/v6/deployments", query={"projectId": "{project_id}", "limit": "{limit}"}, result="deployments", example={"limit": 10}),
    }, "Credential: an access token."),
    _entry("netlify", "Netlify", "https://api.netlify.com/api/v1", BEARER, {
        "sites": _op("List sites", "/sites"),
        "deploys": _op("List deploys of a site", "/sites/{site_id}/deploys", required=["site_id"], example={"site_id": "SITE_ID"}),
        "trigger_build": _op("Start a build", "/sites/{site_id}/builds", "POST", required=["site_id"], example={"site_id": "SITE_ID"}),
    }, "Credential: a personal access token."),
    _entry("digitalocean", "DigitalOcean", "https://api.digitalocean.com/v2", BEARER, {
        "droplets": _op("List droplets", "/droplets", query={"per_page": "{per_page}"}, result="droplets", example={"per_page": 25}),
        "account": _op("Account", "/account", result="account"),
    }, "Credential: a personal access token (read scope is enough)."),
    _entry("sentry", "Sentry", "https://sentry.io/api/0", BEARER, {
        "projects": _op("List projects", "/projects/"),
        "issues": _op("List issues of a project", "/projects/{organization}/{project}/issues/", query={"query": "{query}", "statsPeriod": "{period}"}, required=["organization", "project"], example={"organization": "my-org", "project": "my-project", "query": "is:unresolved"}),
    }, "Credential: an auth token with project:read and event:read."),
    _entry("webflow", "Webflow", "https://api.webflow.com/v2", BEARER, {
        "sites": _op("List sites", "/sites", result="sites"),
        "collections": _op("List collections of a site", "/sites/{site_id}/collections", required=["site_id"], result="collections", example={"site_id": "SITE_ID"}),
        "items": _op("List items of a collection", "/collections/{collection_id}/items", query={"limit": "{limit}"}, required=["collection_id"], result="items", example={"collection_id": "COLLECTION_ID", "limit": 20}),
    }, "Credential: a site API token."),
    _entry("ghost", "Ghost Content API", "https://demo.ghost.io/ghost/api/content", {"type": "query", "name": "key"}, {
        "posts": _op("List posts", "/posts/", query={"limit": "{limit}", "include": "tags,authors"}, result="posts", example={"limit": 5}),
    }, "Edit base_url to your own site, for example https://yoursite.com/ghost/api/content. Credential: a Content API key."),
    _entry("plausible", "Plausible Analytics", "https://plausible.io/api/v1/stats", BEARER, {
        "aggregate": _op("Aggregate stats", "/aggregate", query={"site_id": "{site_id}", "period": "{period}", "metrics": "{metrics}"}, required=["site_id"], result="results", example={"site_id": "example.com", "period": "30d", "metrics": "visitors,pageviews"}),
        "top_pages": _op("Top pages", "/breakdown", query={"site_id": "{site_id}", "period": "{period}", "property": "event:page", "limit": "{limit}"}, required=["site_id"], result="results", example={"site_id": "example.com", "period": "7d", "limit": 10}),
    }, "Credential: a Stats API key. Self-hosted: edit base_url."),
    _entry("coingecko", "CoinGecko prices", "https://api.coingecko.com/api/v3", NONE, {
        "price": _op("Current price of coins", "/simple/price", query={"ids": "{ids}", "vs_currencies": "{vs_currencies}"}, required=["ids", "vs_currencies"], example={"ids": "bitcoin,ethereum", "vs_currencies": "usd,eur"}),
        "markets": _op("Top coins by market cap", "/coins/markets", query={"vs_currency": "{vs_currency}", "per_page": "{per_page}"}, required=["vs_currency"], example={"vs_currency": "usd", "per_page": 10}),
    }, "Free public prices, rate limited. No sign-in."),
    _entry("wikipedia", "Wikipedia summaries", "https://en.wikipedia.org/api/rest_v1", NONE, {
        "summary": _op("Short summary of a page", "/page/summary/{title}", required=["title"], example={"title": "Alan_Turing"}),
    }, "Wikimedia REST API. No sign-in. Change en to another language code in base_url."),
    _entry("dictionary", "Free Dictionary", "https://api.dictionaryapi.dev/api/v2", NONE, {
        "define": _op("Look up an English word", "/entries/en/{word}", required=["word"], example={"word": "hello"}),
    }, "Definitions, phonetics and examples. No sign-in."),
    _entry("openlibrary", "Open Library", "https://openlibrary.org", NONE, {
        "search": _op("Search books", "/search.json", query={"q": "{query}", "limit": "{limit}"}, required=["query"], result="docs", example={"query": "the hobbit", "limit": 5}),
        "isbn": _op("Get a book by ISBN", "/isbn/{isbn}.json", required=["isbn"], example={"isbn": "9780140328721"}),
    }, "Free book data. No sign-in."),
    _entry("spacex", "SpaceX launches", "https://api.spacexdata.com/v4", NONE, {
        "latest": _op("Latest launch", "/launches/latest"),
        "upcoming": _op("Upcoming launches", "/launches/upcoming"),
    }, "Community-run open data. No sign-in."),
    _entry("nager_holidays", "Public holidays (Nager.Date)", "https://date.nager.at/api/v3", NONE, {
        "holidays": _op("Public holidays of a year", "/PublicHolidays/{year}/{country}", required=["year", "country"], example={"year": 2026, "country": "US"}),
        "next": _op("Next public holidays in a country", "/NextPublicHolidays/{country}", required=["country"], example={"country": "GB"}),
    }, "Two-letter country codes. No sign-in."),
    _entry("zippopotam", "Zippopotam postal codes", "https://api.zippopotam.us", NONE, {
        "lookup": _op("Places for a postal code", "/{country}/{postal_code}", required=["country", "postal_code"], example={"country": "us", "postal_code": "90210"}),
    }, "Postal code to city and coordinates. No sign-in."),
    _entry("stackexchange", "Stack Exchange", "https://api.stackexchange.com/2.3", NONE, {
        "search": _op("Search questions by title", "/search", query={"intitle": "{title}", "site": "{site}", "pagesize": "{pagesize}", "order": "desc", "sort": "votes"}, required=["title"], result="items", example={"title": "python decorators", "site": "stackoverflow", "pagesize": 5}),
    }, "Public reads, rate limited without a key. No sign-in."),
    _entry("cloudflare", "Cloudflare", "https://api.cloudflare.com/client/v4", BEARER, {
        "zones": _op("List zones", "/zones", result="result"),
        "dns_records": _op("List DNS records of a zone", "/zones/{zone_id}/dns_records", required=["zone_id"], result="result", example={"zone_id": "ZONE_ID"}),
    }, "Credential: an API token with Zone read permission. Read only here."),
    _entry("postmark", "Postmark email", "https://api.postmarkapp.com", {"type": "header", "name": "X-Postmark-Server-Token"}, {
        "send": _op("Send an email", "/email", "POST", body={"From": "{from}", "To": "{to}", "Subject": "{subject}", "TextBody": "{text}", "MessageStream": "outbound"}, required=["from", "to", "subject", "text"], example={"from": "you@example.com", "to": "them@example.com", "subject": "Hello", "text": "Hi there"}),
    }, "Credential: a server token. The From address must be a verified sender."),
    _entry("brevo", "Brevo (email and contacts)", "https://api.brevo.com/v3", {"type": "header", "name": "api-key"}, {
        "account": _op("Account details", "/account"),
        "contacts": _op("List contacts", "/contacts", query={"limit": "{limit}"}, result="contacts", example={"limit": 20}),
    }, "Credential: a Brevo API key."),
    _entry("lemonsqueezy", "Lemon Squeezy", "https://api.lemonsqueezy.com/v1", BEARER, {
        "orders": _op("List orders", "/orders", query={"page[size]": "{page_size}"}, result="data", example={"page_size": 10}),
        "customers": _op("List customers", "/customers", query={"page[size]": "{page_size}"}, result="data", example={"page_size": 10}),
    }, "Credential: an API key. Replies follow the JSON:API format."),
    _entry("hunter", "Hunter email finder", "https://api.hunter.io/v2", {"type": "query", "name": "api_key"}, {
        "domain_search": _op("Email addresses at a domain", "/domain-search", query={"domain": "{domain}", "limit": "{limit}"}, required=["domain"], result="data", example={"domain": "stripe.com", "limit": 5}),
        "verify": _op("Check an email address", "/email-verifier", query={"email": "{email}"}, required=["email"], result="data", example={"email": "someone@example.com"}),
    }, "Credential: a Hunter API key (a free plan exists)."),
    _entry("sunrise_sunset", "Sunrise and sunset", "https://api.sunrise-sunset.org", NONE, {
        "times": _op("Sunrise and sunset for a place", "/json", query={"lat": "{lat}", "lng": "{lng}", "date": "{date}", "formatted": "0"}, required=["lat", "lng"], result="results", example={"lat": 51.5, "lng": -0.12, "date": "today"}),
    }, "Times are in UTC. No sign-in."),
    _entry("npm", "npm registry", "https://registry.npmjs.org", NONE, {
        "package": _op("Everything about a package", "/{package}", required=["package"], example={"package": "react"}),
        "latest": _op("Latest version of a package", "/{package}/latest", required=["package"], example={"package": "react"}),
    }, "Public package data. No sign-in. The full package reply can be large, so the latest version is usually what you want."),
    _entry("pypi", "PyPI", "https://pypi.org/pypi", NONE, {
        "project": _op("Details of a Python package", "/{project}/json", required=["project"], example={"project": "fastapi"}),
    }, "Public package data. No sign-in."),
    _entry("dockerhub", "Docker Hub", "https://hub.docker.com/v2", NONE, {
        "repo": _op("Details of a public repository", "/repositories/{namespace}/{repo}", required=["namespace", "repo"], example={"namespace": "library", "repo": "nginx"}),
        "tags": _op("Tags of a public repository", "/repositories/{namespace}/{repo}/tags", query={"page_size": "{page_size}"}, required=["namespace", "repo"], result="results", example={"namespace": "library", "repo": "nginx", "page_size": 10}),
    }, "Public images only. No sign-in. Use library as the namespace for official images."),
    _entry("agify", "Agify (age from a name)", "https://api.agify.io", NONE, {
        "guess": _op("Likely age for a first name", "/", query={"name": "{name}", "country_id": "{country_id}"}, required=["name"], example={"name": "michael"}),
    }, "A statistical guess from a first name. Free tier is rate limited. No sign-in."),
    _entry("genderize", "Genderize (from a name)", "https://api.genderize.io", NONE, {
        "guess": _op("Likely gender for a first name", "/", query={"name": "{name}", "country_id": "{country_id}"}, required=["name"], example={"name": "alex"}),
    }, "A statistical guess from a first name, not a fact about a person. Free tier is rate limited. No sign-in."),
    _entry("nationalize", "Nationalize (from a name)", "https://api.nationalize.io", NONE, {
        "guess": _op("Likely countries for a last name", "/", query={"name": "{name}"}, required=["name"], example={"name": "nielsen"}),
    }, "A statistical guess from a name, not a fact about a person. Free tier is rate limited. No sign-in."),
    _entry("catfact", "Cat facts", "https://catfact.ninja", NONE, {
        "fact": _op("One random cat fact", "/fact"),
        "facts": _op("Several cat facts", "/facts", query={"limit": "{limit}"}, result="data", example={"limit": 3}),
    }, "A small fun API, handy for testing a workflow end to end. No sign-in."),
    _entry("dogceo", "Dog pictures", "https://dog.ceo/api", NONE, {
        "random": _op("A random dog picture", "/breeds/image/random"),
        "breed": _op("A random picture of one breed", "/breed/{breed}/images/random", required=["breed"], example={"breed": "beagle"}),
    }, "Returns a picture address. No sign-in."),
    _entry("crossref", "Crossref (research papers)", "https://api.crossref.org", NONE, {
        "works": _op("Search published works", "/works", query={"query": "{query}", "rows": "{rows}"}, required=["query"], result="message", example={"query": "workflow automation", "rows": 5}),
    }, "Scholarly metadata for papers and books. No sign-in. Add a contact address to the query in a workflow if you make many calls."),
    _entry("er_api", "Exchange rates (open access)", "https://open.er-api.com/v6", NONE, {
        "latest": _op("Rates for a base currency", "/latest/{base}", required=["base"], example={"base": "USD"}),
    }, "Daily rates, free for light use. No sign-in."),
    _entry("randomuser", "Random User (test people)", "https://randomuser.me", NONE, {
        "users": _op("Made-up people for testing", "/api/", query={"results": "{results}", "nat": "{nat}"}, result="results", example={"results": 3}),
    }, "Fake names and addresses for testing a workflow. No sign-in."),
    _entry("officialjoke", "Official Joke API", "https://official-joke-api.appspot.com", NONE, {
        "random": _op("A random joke", "/random_joke"),
        "ten": _op("Ten random jokes", "/jokes/ten"),
    }, "A small fun API, handy for testing a workflow end to end. No sign-in."),
]
