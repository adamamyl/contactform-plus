We're going to build some things for the EMF Festival, which may be used by other festivals, and will be open-sourced.

Each might be separate, but connected for other uses. 

EMF seems to favour Python, Postgres for databases, and our approach will be to dockerize for deployment to a VPS (as well as for local testing). For local testing we should mock the oauth/sso flow, or standup a very simple keycloak/uffd. We should use `uv` (from astral) for package management, venv creation, and all that lifting. We should use similar approaches for reproducable code in other languages.

Even though we will be dockerized, we will need to ensure we use TLS between components. Ensure this is baked in, principle of least privilege, and other good hygiene.

We will test as we go to ensure we don't introduce any issues -- bandit, ruff, etc. Setup our repo(s) from the start so we do not commit creds (gitleaks/gitsecrets).

Security must be baked in from the start, so ensure that in tests we explictiy test against OWASP Top 10 2025. Amongst others, our community includes a large amount of inquisitve programmers, nerds, people looking for exploits -- let's defeat them by design, not obscurity :) We do not trust user input at all.

We will not write functionality where (maintained) libraries/modules exist. Our first question should be "is there a library that does this already" rather than writing unnecessary code. 

We will support emoji.

Use material design objects. And fonts, css and the like should be downloaded locally, not served from google or other cdns. For the user app, we don't want to set cookies unless absolutely needed. We don't need webstats. 

Support a simple navigation menu `<HOME>` (top left). At the bottom support a navigation menu e.g. `privacy policy | code of conduct | about | map`

We will want a fully working local deployment, using local hostnames, as well as prod ones -- it's probably sensible to use caddy for this. For local testing, we should still test with https; self-signed certs here are acceptable.

We will only serve on HTTP/2 and TLS 1.3. We don't need to be backwards compatible. 

For speed/simplicity, we probably want to set variables in an .env file / some json (I hate yaml/toml), but if we 'up' this in the future, it might make sense for these to be configurable per-team/use, and editable from a web interface. 

All web things must be responsive; and not suck on the phone. Text sizes should be readable and follow accessibility European guidelines. https://www.gov.uk/service-manual/helping-people-to-use-your-service 

We'll create README.md for each, along with CLAUDE.md, and a refined plan.md. 

## Observability
 - We will want a way to monitor all components' health, that'll be used by the sysadmin team
 - The EMF stack is grafana; we want to supply the payloads to build those dashboards
 - Abuses (e.g. a lot of submissions from one place) will probably want to be picked up, so they can be stopped.


## Our apps will be:
### 1. A small web-app to handle code of conduct (or other) complaints.

    It will need to be aware of three phases:
        - pre-event
        - event time
        - post-event

    as different actions will be available during event time. The event dates should be sourced from config, e.g.,
       {
        events:
            { 
                name: "emfcamp2026",
                startDate: 2026-07-12,
                endDate: 2026-07-20
            },
            {
                name: "emfcamp2028",
                startDate: 2028-07-05,
                endDate: 2028-07-10
            }
       }

    We'll need some flexibility (in config) for the days before/after event the team are on-site, when we'll still want to use signal, but the phone system is likely to not be available.

    This should guide the user through filling out their details and the details of the complaint. (front-end). We'll refine the other features of this, but it would probably be useful to give people the option to receive the details of their case by email.

    We don't need bloat, we need simple, efficient, trustworthy.

    THe user should be able to set the priority of their call, so it can be handled appropriately.

    We will collect the least amount of data possible, fitting in with our approach to data minimalization.

    The results must be saved to a small database, including a 
     - case-number (uuid),
     - unique friendly name (four random words, lowercased and hypen-separated; cardinality of 1 to uuid)
     - the details from the form in structured data (but aware things may change, so maybe we store the responses as a self-documenting json object? )
     - other things we need for the conduct team app

    It should be easy to add new questions -- and store them properly.

    For others' use we might want to think about i18n, but let's park that as a nice-to-have

    #### The existing form:

        # Accessibility & Conduct Contact form
        ## Section 1
        [Text]
        ``` 
        Please use this form to report an issue to the EMF Accessibility & Conduct Team. 

        This could be an incident or a potential breach of the code of conduct. 

        For urgent event-time issues, please use the DECT phone network (1234) to call the Accessibility & Conduct Team, or ask someone with a radio to contact the Accessibility & Conduct Team. We'll be able to react a lot faster.
        ```

        Q: Who is reporting this?
        [descr] Include a name (if you wish), contact details (so we can get in touch)
            - (festivals) name/persona <short text>
            - pronouns <short text>
            - phone <short text>
            - email address <valid email address>
            - camping with… <short text>

        Q: What are you reporting <long text>
        [descr] Please try to be impartial here "I was by… when I saw…"
        
        Q: When did this take place? <date picker; use locale for format>
        
        Q: Time (24hr; HH:MM; default to now)

        Q: Where did this take place <short text>, use location if enabled?
        [descr] If not sure exactly where, please try to help us — map.emfcamp.org may be useful.
        
        Q Any more information? <long text>
        [descr] if you took a photo / video and want to share that with us, a link is fine.

        Q: Do you feel you need any additional support from anyone at EMF that you don't already have? <long text>
        [descr] First Aid may be able to help, along with the Info Desk. 
        Please call us if you're able to and would like someone to listen.
        
        ## Section 2
        [text] These additional questions could help us, and can be filled in later if you wish.
        Description (optional)

        Q: Were other people involved? <long text>
        [descr] If you know their names, please let us know, but descriptions may also be helpful
        
        Q: Do you know why this happened? <long text>
        
        Q: Can we contact you for more information? <Yes/No>
                
        Q: Is there anything else we should be aware of? <long text>

    We probably want something sensible to prevent webbots/AI agents going crazy with the forms… as it will be available over t'internet.

### 2. A small web-app for the conduct team to review and work on the cases raised. 
    
    It doesn't need to have workflows, but it would be useful to support tags to categorize the complaint (and suggest from ones added already), assign them to someone, and set a status like "new, assigned, in progress, transfered, action needed, decision needed, closed"

    This (and the underlying data) must only be accessible to (configurable) `team_conduct` using EMF single-sign-on. 

#### 2b. Dispatcher app
    A separate view for a dispatcher to see the case details (anonymized) and the option to trigger a call/signal message and transition the state, without seeing the details beyond `urgency`, `friendly-id`, `status`.
    
    In addition to supporting SSO login, from a defined (in config) group, this should also support a short time-linked session URL that can be created/shared from the admin or conduct team panel/app. The session time should be configurable, and be valid for two concurrent devices. After the end, the session must be terminated and access cut-off. Permissions and proper security should ensure that nothing else is available.

#### 2 -- future
    We might want to make this less specific, more generic, e.g. for other teams to use without deploying their own stack. Let's consider it by design to be multi-tenanted, using row-based permissions, but for now, we can keep it bespoke, but making it easier in the future to admin, and let teams create their own forms and config would be useful.

### 2c. Admin app?
    Do we need an admin app that allows use to do things with groups -> forms -> databases, manage the SSO, and other sysaadmin things -- that doesn't expose access to the info stored in the database, but does allow provisioning new teams / managing the app?

    Rather than separate apps, maybe this is a feature of the 'orga' (internal) app -- if you're a sysadmin you can do sysadmin stuff, if you're conduct you can do conduct stuff, if you're a dispatcher, you can do dispatcher stuff, if you're on a site manager you can use the dispatcher dash.
    
### 3. A router system
    We will need to notify the (conduct team) when there's a new case. 
    
    During event time, it's likely the team will be busy, so we will need to make use of 
     - the on-site telephony systems (not public networks as signal is rubbish). This should be abstracted, in case the systems change.
     - sending to a signal group (configurable), possibly via https://github.com/bbernhard/signal-cli-rest-api
     - possibly in a Mattermost channel
     - email threads (via freescout API so we can update the ticket)

     With multi-channel approaches, it's critical that we update one when something's done so we don't duplicate efforts. We should use webhooks to update things, and transition the states, which should cause less-noisy approaches (e.g. an emoji response to the message in signal/mattermost)

    If sending using multiple means, we should explicitly say "Calling … also". "XYZ ACK'd the call" (maybe via consistent emoji, to avoid death-by-notifications/alert-fatigue)

    Outside of event time, only sending emails is fine.

    We should check the site phone system is available and functional, before using it -- it's a `best efforts` system. If it's unavailable, and it's urgent, use signal.

    For event time form submissions -> alert, we should abstract the approaches to be technology agnostic; the 'event specific tooling' should take our generic functions and do the plumbing with the end system (e.g. from our router -> jambonz or router -> twilio)


    The router system should do these generic/high-level tasks
     -- e.g.
        - do text-to-speech
        - send emails
        - make a call
        - post in a signal group
        - post in a mattermost channel
        - retry
        - ensure ACK by someone
        - handle updates to the system to avoid duplication of effort/alert fatigue.

    At event time, we will also want to send an email to track the case, but aware it may not be looked at right away.

    Additionally, if multiple email addresses are configured, send to them all -- e.g. it may be that event-time-dispatcher@emfcamp.org gets the non-sensitive information to route (manually) to people on duty. 

    Outside of event time, it's fine to send a link to the case and some other details (to be designed together) by email to a configurable email address, e.g. conduct@emfcamp.org

    This will need to be reliable, and stateful -- has the message been sent, is it in a retry queue, did it fail to send, has it been picked up by someone. 

### 4. Text-to-speech
    For the telephony piece, we will want to use text to speech to convey the pertinent information, routing that to the phone system, for it to do the calling. This should be clear, succinct, and give the details as a short message, conveying the important things : "new URGENT case: <type> at <location>." Additionally we will want to handle something like "Press 1 to ACK, 2 to call next person" in the routing/tooling

### 5. The event specific tooling. 
    For EMF 2026, the phone system will be working with Jambonz. You will need to understand the capabilities of the API via https://docs.jambonz.org/reference/introduction and then have some code that:

        - uses the API or SDK, depending on features needed
        - takes the text-to-speech data stream
        - routes it to the jambonz system
        - sends it on the conduct team call group
        - if not picked up straight away, try again 3 times at 5, 10, 15 min intervals
        - escalate to a shift-leader; the number will stay the same, as the phone is passed around but this should be configured
        - and if necessary, the person on-shift for site (for urgent matters only)
        - 'escalate' to the lead (this will be a personal number, so configurable)
        

    The router will give 'standard' info ready for plumbing into another system; this specific code will be for handling from the router to jambonz -- we'll model on that, but be aware it might be replaced, so we need to abstract things, and do the plumbing outselves. This part of code is most likely to be thrown-away. 
